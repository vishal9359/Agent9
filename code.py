import os
import json
import subprocess
import re
from collections import defaultdict
from clang import cindex
from docx import Document
from docx.shared import Inches
from langchain_ollama import ChatOllama
from langchain.messages import HumanMessage

# OPTIONAL: Set explicitly if needed
# cindex.Config.set_library_file("/usr/lib/llvm-18/lib/libclang.so")

file_index_map = {}
SUPPORTED_EXT = (".c", ".cpp", ".cc", ".cxx")

# Configure LLM with optimized settings
llm = ChatOllama(model="gpt-oss", temperature=0.3, top_k=10, top_p=0.9)

# Configure paths - UPDATE THESE FOR YOUR ENVIRONMENT
mermaid_path = "/home/workspace/mermaid_converter"
out_dir = "/home/workspace/adk/gemma-code/orchestrator/docs"


def is_cpp_file(path):
    return path.endswith(SUPPORTED_EXT)


def get_module_name(file_path, root_dir):
    rel = os.path.relpath(file_path, root_dir)
    no_ext = os.path.splitext(rel)[0]
    return ".".join(no_ext.split(os.sep))


def node_uid(cursor):
    """Stable unique identifier for functions/methods"""
    loc = cursor.location
    return f"{cursor.spelling}:{loc.file.name}:{loc.line}"


def clean_unicode_chars(text):
    """Remove or replace Unicode characters with ASCII equivalents"""
    if not text:
        return ""
    ascii_text = text.encode('ascii', 'ignore').decode('ascii')
    ascii_text = re.sub(r'[^\x20-\x7E\n\r\t]', '', ascii_text)
    return ascii_text


def extract_control_flow_structure(function_cursor, file_lines, start_line):
    """
    Extract control flow structure from function AST
    Returns a simplified representation focusing on control flow
    """
    control_flow = []
    
    def analyze_node(cursor, indent_level=0):
        """Recursively analyze AST nodes for control flow"""
        kind = cursor.kind
        
        # CONTROL FLOW NODES (always include)
        if kind == cindex.CursorKind.IF_STMT:
            # Get condition
            children = list(cursor.get_children())
            if len(children) >= 1:
                cond_start = children[0].extent.start.line
                cond_end = children[0].extent.end.line
                condition_text = " ".join(file_lines[cond_start - start_line:cond_end - start_line + 1])
                control_flow.append({
                    'type': 'if',
                    'condition': condition_text.strip(),
                    'line': cursor.extent.start.line
                })
            
            # Recurse into if and else blocks
            for child in children[1:]:
                analyze_node(child, indent_level + 1)
        
        elif kind in (cindex.CursorKind.FOR_STMT, cindex.CursorKind.WHILE_STMT, cindex.CursorKind.DO_STMT):
            loop_type = 'for' if kind == cindex.CursorKind.FOR_STMT else 'while' if kind == cindex.CursorKind.WHILE_STMT else 'do-while'
            children = list(cursor.get_children())
            condition_text = ""
            if children:
                cond_start = children[0].extent.start.line
                cond_end = children[0].extent.end.line
                condition_text = " ".join(file_lines[cond_start - start_line:cond_end - start_line + 1])
            
            control_flow.append({
                'type': 'loop',
                'loop_type': loop_type,
                'condition': condition_text.strip(),
                'line': cursor.extent.start.line
            })
            
            # Recurse into loop body
            for child in children:
                analyze_node(child, indent_level + 1)
        
        elif kind == cindex.CursorKind.SWITCH_STMT:
            children = list(cursor.get_children())
            switch_var = ""
            if children:
                switch_var = " ".join(file_lines[children[0].extent.start.line - start_line:children[0].extent.end.line - start_line + 1])
            
            control_flow.append({
                'type': 'switch',
                'variable': switch_var.strip(),
                'line': cursor.extent.start.line
            })
            
            # Recurse into cases
            for child in children[1:]:
                analyze_node(child, indent_level + 1)
        
        elif kind == cindex.CursorKind.CASE_STMT:
            control_flow.append({
                'type': 'case',
                'line': cursor.extent.start.line
            })
            for child in cursor.get_children():
                analyze_node(child, indent_level)
        
        elif kind == cindex.CursorKind.DEFAULT_STMT:
            control_flow.append({
                'type': 'default',
                'line': cursor.extent.start.line
            })
            for child in cursor.get_children():
                analyze_node(child, indent_level)
        
        elif kind == cindex.CursorKind.RETURN_STMT:
            control_flow.append({
                'type': 'return',
                'line': cursor.extent.start.line
            })
        
        elif kind == cindex.CursorKind.BREAK_STMT:
            control_flow.append({
                'type': 'break',
                'line': cursor.extent.start.line
            })
        
        elif kind == cindex.CursorKind.CONTINUE_STMT:
            control_flow.append({
                'type': 'continue',
                'line': cursor.extent.start.line
            })
        
        elif kind == cindex.CursorKind.CALL_EXPR:
            # Function calls are important control flow elements
            ref = cursor.referenced
            if ref and ref.spelling:
                control_flow.append({
                    'type': 'call',
                    'function': ref.spelling,
                    'line': cursor.extent.start.line
                })
        
        else:
            # For other nodes, recurse into children
            for child in cursor.get_children():
                analyze_node(child, indent_level)
    
    # Start analysis
    for child in function_cursor.get_children():
        analyze_node(child, 0)
    
    return control_flow


def create_control_flow_summary(control_flow, function_lines):
    """
    Create a summary of control flow for LLM
    Groups sequential statements into semantic blocks
    """
    if not control_flow:
        # For functions with no control flow, summarize the entire function
        return "Sequential function with no branches or loops. Summarize the overall purpose."
    
    summary_parts = []
    summary_parts.append("Control flow structure:")
    
    for item in control_flow:
        if item['type'] == 'if':
            summary_parts.append(f"  - IF condition at line {item['line']}")
        elif item['type'] == 'loop':
            summary_parts.append(f"  - {item['loop_type'].upper()} loop at line {item['line']}")
        elif item['type'] == 'switch':
            summary_parts.append(f"  - SWITCH statement at line {item['line']}")
        elif item['type'] == 'case':
            summary_parts.append(f"    - CASE at line {item['line']}")
        elif item['type'] == 'default':
            summary_parts.append(f"    - DEFAULT at line {item['line']}")
        elif item['type'] == 'return':
            summary_parts.append(f"  - RETURN at line {item['line']}")
        elif item['type'] == 'call':
            summary_parts.append(f"  - Call {item['function']}() at line {item['line']}")
    
    return "\n".join(summary_parts)


def extract_flowchart_from_response(response_content):
    """Extract flowchart code from LLM response"""
    if not response_content:
        return ""
        
    response_content = response_content.strip()
    
    if "```mermaid" in response_content:
        parts = response_content.split("```mermaid")
        if len(parts) > 1:
            flowchart = parts[1].split("```")[0].strip()
            return flowchart
    
    if "```" in response_content:
        parts = response_content.split("```")
        for part in parts:
            if "flowchart" in part.lower():
                return part.strip()
    
    if "flowchart" in response_content.lower():
        lines = response_content.split("\n")
        flowchart_lines = []
        started = False
        for line in lines:
            if "flowchart" in line.lower() and ("td" in line.lower() or "lr" in line.lower()):
                started = True
            
            if started:
                line_stripped = line.strip()
                if line_stripped and re.match(r'^[A-Z][a-z]+[:\.]', line_stripped):
                    break
                flowchart_lines.append(line)
        
        if flowchart_lines:
            return "\n".join(flowchart_lines).strip()
    
    return response_content.strip()


def replace_brackets_in_brackets(text):
    """Replace nested brackets with HTML entities"""
    result = []
    depth = 0

    for char in text:
        if char in "[{":
            if depth == 0:
                result.append(char)
            else:
                result.append("&#91;" if char == "[" else "&#123;")
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth < 0:
                depth = 0
            if depth == 0:
                result.append(char)
            else:
                result.append("&#93;" if char == "]" else "&#125;")
        else:
            result.append(char)

    return "".join(result)


def clean_label_text(label):
    """Clean label text to avoid Mermaid parse errors and HTML entities."""
    if not label:
        return ""

    label = clean_unicode_chars(label)

    # Replace common HTML entities if they appear
    entity_map = {
        "&#40;": " ",
        "&#41;": " ",
        "&#91;": " ",
        "&#93;": " ",
        "&#123;": " ",
        "&#125;": " ",
        "&amp;": " and ",
    }
    for entity, replacement in entity_map.items():
        label = label.replace(entity, replacement)

    # Replace operators with words
    label = (
        label.replace("!=", " not equal ")
        .replace("==", " equal ")
        .replace(">=", " greater or equal ")
        .replace("<=", " less or equal ")
        .replace(">", " greater ")
        .replace("<", " less ")
        .replace("&&", " and ")
        .replace("||", " or ")
    )

    # Replace function call syntax like foo(bar) -> call foo
    label = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)", r"call \1", label)

    # Remove punctuation that causes Mermaid parse errors
    label = re.sub(r"[;:]", " ", label)
    label = re.sub(r"[{}()\[\]]", " ", label)
    label = label.replace("?", " ")

    # Collapse whitespace
    label = re.sub(r"\s+", " ", label).strip()

    return label


def sanitize_flowchart_content(flowchart_content):
    """Sanitize flowchart content to be Mermaid-compatible"""
    if not flowchart_content:
        return ""
    
    flowchart_content = clean_unicode_chars(flowchart_content)
    if not flowchart_content:
        return ""
    
    # Fix single braces to double braces for decision nodes
    flowchart_content = re.sub(r'(n\d+)\{([^}]*)\}(?!\})', r'\1{{\2}}', flowchart_content)
    
    flowchart_list = flowchart_content.split("\n")
    new_list = []
    flowchart_started = False

    for line in flowchart_list:
        line_lower = line.lower().strip()
        
        if ("flowchart td" in line_lower or "flowchart lr" in line_lower) and not flowchart_started:
            flowchart_started = True
            new_list.append("flowchart LR" if "lr" in line_lower else "flowchart TD")
            continue

        if not flowchart_started:
            continue

        if not line.strip():
            continue
        
        if line.strip().startswith(('```', 'Note:', 'Explanation:', '##', '# ', '---')):
            break

        original_line = line.strip()
        
        if "flowchart" in original_line.lower():
            continue
            
        processed_line = original_line
        
        # Don't replace parentheses with HTML entities
        # Instead, just remove them from node labels (except Start/End)
        # Keep them in edge labels
        
        # Clean decision labels {{...}}
        decision_parts = re.findall(r"\{\{([^}]*)\}\}", processed_line)
        for part in decision_parts:
            cleaned_part = clean_label_text(part)
            processed_line = processed_line.replace(f"{{{{{part}}}}}", f"{{{{{cleaned_part}}}}}")

        # Clean process labels [...]
        process_parts = re.findall(r"\[([^\]]+)\]", processed_line)
        for part in process_parts:
            cleaned_part = clean_label_text(part)
            processed_line = processed_line.replace(f"[{part}]", f"[{cleaned_part}]")
        
        new_list.append(processed_line)

    return "\n".join(new_list) if new_list else ""


def validate_mermaid_syntax(mermaid_content):
    """Validate Mermaid syntax"""
    if not mermaid_content or not mermaid_content.strip():
        return False, "Empty flowchart content"

    if "flowchart" not in mermaid_content.lower():
        return False, "Missing flowchart declaration"

    if "Start" not in mermaid_content and "start" not in mermaid_content:
        return False, "Missing Start node"
    
    if "End" not in mermaid_content and "end" not in mermaid_content.lower():
        return False, "Missing End node"

    if "-->" not in mermaid_content and "--" not in mermaid_content:
        return False, "No connections found"

    if "&#" in mermaid_content:
        return False, "HTML entities detected in labels. Use plain text only."

    lines = [l.strip() for l in mermaid_content.split("\n") if l.strip()]
    if len(lines) < 3:
        return False, f"Too few lines: {len(lines)}"

    for line in lines:
        if line.count("-->") > 1:
            return False, "Multiple arrows in one line. Use one edge per line."
    
    # Check for unlabeled nodes
    defined_nodes = set()
    used_nodes = set()
    
    for line in lines:
        if "flowchart" in line.lower():
            continue
        
        defined = re.findall(r'\b(n\d+)\s*[\[\{]', line)
        defined_nodes.update(defined)
        
        used = re.findall(r'(?:-->|--)\s+(n\d+)(?:\s|$|-->|--|\|)', line)
        used_nodes.update(used)
        used_before = re.findall(r'\b(n\d+)\s+(?:-->|--)', line)
        used_nodes.update(used_before)
    
    unlabeled = used_nodes - defined_nodes
    
    if unlabeled:
        return False, f"Nodes without labels: {', '.join(sorted(list(unlabeled))[:5])}. Use n1[Label] or n2{{{{Condition}}}}"

    return True, None


def extract_function_calls(function_content):
    """Extract function calls from code"""
    function_calls = []
    pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    matches = re.findall(pattern, "\n".join(function_content))
    
    keywords = {
        'if', 'while', 'for', 'switch', 'return', 'new', 'delete', 'sizeof',
        'static_cast', 'dynamic_cast', 'const_cast', 'reinterpret_cast',
        'printf', 'sprintf', 'fprintf', 'assert', 'memset', 'memcpy', 'free', 'malloc',
        'std', 'cout', 'endl'
    }
    
    seen = set()
    for match in matches:
        if match not in keywords and match not in seen:
            function_calls.append(match)
            seen.add(match)
    
    return function_calls


def generate_function_description(function_content):
    """Generate description using LLM"""
    prompt = (
        "You are a C++ code documentation expert.\n\n"
        "Analyze the following C++ function and provide a clear, concise description.\n"
        "Describe what the function does, its purpose, and key operations.\n"
        "Be accurate - only describe what you see in the code.\n"
        "Keep it to 2-3 sentences.\n\n"
        "Function code:\n{function}\n\nDescription:"
    )

    query = prompt.format(function="\n".join(function_content[:50]))
    messages = [HumanMessage(query)]
    
    try:
        response = llm.invoke(messages)
        return response.content.strip()
    except Exception as e:
        print(f"Error generating description: {e}")
        return "Description generation failed"


def generate_flowchart(function_content, function_name, function_cursor=None, file_lines=None, start_line=0):
    """Generate flowchart focusing on CONTROL FLOW, not every statement"""
    
    function_calls = extract_function_calls(function_content)
    function_calls_str = ", ".join(function_calls[:5]) if function_calls else "none"
    
    # Extract control flow structure from AST if available
    if function_cursor and file_lines:
        control_flow = extract_control_flow_structure(function_cursor, file_lines, start_line)
        control_summary = create_control_flow_summary(control_flow, function_content)
    else:
        control_summary = "Analyze the code to identify control flow"
    
    flowchart_prompt = (
        "Create a Mermaid flowchart for this C++ function.\n\n"
        "FLOWCHART PURPOSE: Show CONTROL FLOW, not every statement\n\n"
        "INCLUDE in flowchart:\n"
        "1. Branches: if/else, switch/case\n"
        "2. Loops: for, while, do-while\n"
        "3. Function calls (as single box, do NOT expand)\n"
        "4. Return statements\n"
        "5. Sequential blocks (summarized as ONE node)\n\n"
        "EXCLUDE from flowchart:\n"
        "- Individual variable assignments\n"
        "- Arithmetic operations\n"
        "- Iterator increments\n"
        "- Temporary variables\n"
        "- Logging statements\n\n"
        "GROUPING RULE:\n"
        "Group 3-5 sequential statements into ONE semantic node\n"
        "Example: Instead of separate nodes for a=1, b=2, c=3\n"
        "Use ONE node: [Initialize variables a, b, c]\n\n"
        "FORMAT RULES:\n"
        "1. Start with: flowchart TD\n"
        "2. Start node: Start((Start))\n"
        "3. End node: End((End))\n"
        "4. Process: n1[Semantic description]\n"
        "5. Decision: n2{{{{Condition in words}}}}\n"
        "6. ALL nodes MUST have labels\n"
        "7. NO operators in labels (use words)\n"
        "8. NO parentheses in labels: use 'Call function' not 'function()'\n"
        "9. Each arrow connection on separate line for clarity\n\n"
        f"Detected function calls: {function_calls_str}\n"
        f"Control flow summary:\n{control_summary}\n\n"
        "EXAMPLE (Notice grouping of sequential operations):\n"
        "flowchart TD\n"
        "Start((Start)) --> n1[Initialize data structures]\n"
        "n1 --> n2{{{{Check if input valid}}}}\n"
        "n2 --> |true| n3[Process data in loop]\n"
        "n2 --> |false| n4[Return error]\n"
        "n3 --> n5[Finalize and return result]\n"
        "n4 --> End((End))\n"
        "n5 --> End((End))\n\n"
        "Function code:\n{function}\n\n"
        "Generate flowchart (focus on control flow, group sequential statements):"
    )

    print(f"\nGenerating flowchart for: {function_name} ({len(function_content)} lines)")
    query = flowchart_prompt.format(function="\n".join(function_content))
    
    retries = 0
    max_retries = 5
    last_error = None

    while retries < max_retries:
        try:
            messages = [HumanMessage(query)]
            response = llm.invoke(messages)
            raw_content = response.content
            
            if not raw_content or not raw_content.strip():
                last_error = "LLM returned empty response"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                continue

            extracted = extract_flowchart_from_response(raw_content)
            
            if not extracted or not extracted.strip():
                last_error = "Could not extract flowchart"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                continue

            sanitized = sanitize_flowchart_content(extracted)

            if not sanitized or not sanitized.strip():
                last_error = "Flowchart empty after sanitization"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                continue

            is_valid, error_msg = validate_mermaid_syntax(sanitized)
            
            if not is_valid:
                last_error = f"Validation failed: {error_msg}"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                query = flowchart_prompt + f"\n\nPREVIOUS FAILED: {error_msg}\nFix this."
                continue

            # Generate image
            currdir = os.getcwd()
            try:
                os.chdir(mermaid_path)

                out = subprocess.check_output(
                    ["node", "index.js", sanitized, f"{function_name}.png"],
                    stderr=subprocess.STDOUT,
                    timeout=30
                )
                
                os.chdir(currdir)

                if "error" not in str(out.lower()):
                    img_path = os.path.join(mermaid_path, f"{function_name}.png")
                    if os.path.exists(img_path):
                        print(f"✓ Success")
                        return sanitized, img_path, "Success"
                    else:
                        last_error = "Image not created"
                else:
                    last_error = f"Mermaid error: {str(out)[:100]}"
                print(f"Attempt {retries + 1}: {last_error}")
                    
            except Exception as e:
                os.chdir(currdir)
                last_error = f"Image generation error: {str(e)[:100]}"
                print(f"Attempt {retries + 1}: {last_error}")

            retries += 1
            if retries < max_retries:
                query = flowchart_prompt + f"\n\nPREVIOUS FAILED: {last_error}\nFix it."

        except Exception as e:
            print(f"Exception attempt {retries + 1}: {e}")
            last_error = f"Exception: {str(e)[:100]}"
            retries += 1

    print(f"✗ Failed after {max_retries} attempts: {last_error}")
    return None, None, last_error or "Failed"


def extract_node_info(cursor, file_path, module_name):
    """Extract function information from AST"""
    extent = cursor.extent
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        start_line = max(0, extent.start.line - 1)
        end_line = min(len(lines), extent.end.line)
        function_lines = [line.rstrip() for line in lines[start_line:end_line] if line.strip()]

        if not function_lines:
            return None

        print(f"\nProcessing: {cursor.spelling} ({len(function_lines)} lines)")

        # Pass cursor and file lines for control flow analysis
        flowchart, img_path, feedback = generate_flowchart(
            function_lines, cursor.spelling, cursor, lines, extent.start.line
        )

        description = generate_function_description(function_lines)

        return {
            "uid": node_uid(cursor),
            "name": cursor.spelling,
            "line_start": extent.start.line,
            "column_start": extent.start.column,
            "line_end": extent.end.line,
            "column_end": extent.end.column,
            "file_name": file_path,
            "module_name": module_name,
            "description": description,
            "flowchart": flowchart,
            "feedback": feedback,
            "img": img_path,
            "callees": [],
            "callers": [],
        }
    except Exception as e:
        print(f"Error extracting {cursor.spelling}: {e}")
        return None


def visit(cursor, file_path, module_name, nodes, call_edges, current_fn, visited=None):
    """Visit AST nodes"""
    if visited is None:
        visited = set()

    if cursor.location.file and cursor.location.file.name != file_path:
        return

    fqn = f"{module_name}::{file_path}::{cursor.spelling}"

    if fqn in visited:
        return

    if cursor.is_definition():
        if cursor.kind in (
            cindex.CursorKind.FUNCTION_DECL,
            cindex.CursorKind.CXX_METHOD,
            cindex.CursorKind.CONSTRUCTOR,
            cindex.CursorKind.DESTRUCTOR,
        ) and cursor.spelling:
            visited.add(fqn)
            uid = node_uid(cursor)
            
            if uid not in nodes:
                node_info = extract_node_info(cursor, file_path, module_name)
                if node_info:
                    nodes[uid] = node_info
                    current_fn = uid

    if cursor.kind == cindex.CursorKind.CALL_EXPR and current_fn:
        ref = cursor.referenced
        if ref and ref.spelling and ref.location.file:
            callee_uid = node_uid(ref)
            call_edges[current_fn].add(callee_uid)
            
            if current_fn in nodes:
                if callee_uid not in nodes[current_fn]["callees"]:
                    nodes[current_fn]["callees"].append(callee_uid)

    for child in cursor.get_children():
        visit(child, file_path, module_name, nodes, call_edges, current_fn, visited)


def parse_file(index, file_path, root_dir, compile_args, nodes, call_edges):
    """Parse a single C++ file"""
    module_name = get_module_name(file_path, root_dir)

    try:
        print(f"\n{'='*60}")
        print(f"Parsing: {os.path.basename(file_path)}")
        print(f"{'='*60}")

        tu = index.parse(
            file_path,
            args=compile_args,
            options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD,
        )

        my_nodes = {}
        my_call_edges = defaultdict(set)

        visit(tu.cursor, file_path, module_name, my_nodes, my_call_edges, None)

        if not my_nodes:
            print(f"⚠ No functions found")
            return

        file_name = os.path.splitext(os.path.basename(file_path))[0]
        meta_file_name = file_name

        if file_name in file_index_map:
            meta_file_name = f"{file_name}_{file_index_map[file_name]}"
            file_index_map[file_name] += 1
        else:
            file_index_map[file_name] = 1

        json_file = os.path.join(out_dir, f"{meta_file_name}.json")
        docx_file = os.path.join(out_dir, f"{file_name}.docx")

        print(f"\nOutputs: {os.path.basename(docx_file)}, {os.path.basename(json_file)}")

        generate_word_document(list(my_nodes.values()), docx_file)

        with open(json_file, "w", encoding='utf-8') as f:
            json.dump(list(my_nodes.values()), f, indent=2, ensure_ascii=False)

        print(f"✓ Complete: {len(my_nodes)} functions")

        nodes.update(my_nodes)
        call_edges.update(my_call_edges)

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()


def parse_codebase(root_dir, compile_args=None):
    """Parse C++ codebase"""
    compile_args = compile_args or ["-std=c++17"]
    index = cindex.Index.create()

    nodes = {}
    call_edges = defaultdict(set)

    cpp_files = []
    for root, _, files in os.walk(root_dir):
        for f in files:
            if is_cpp_file(f):
                cpp_files.append(os.path.join(root, f))

    print(f"\n{'='*60}")
    print(f"ANALYSIS STARTED")
    print(f"{'='*60}")
    print(f"Root: {root_dir}")
    print(f"Files: {len(cpp_files)}")
    print(f"Output: {out_dir}")
    print(f"{'='*60}\n")

    for idx, path in enumerate(cpp_files, 1):
        print(f"\n[{idx}/{len(cpp_files)}] {os.path.relpath(path, root_dir)}")
        try:
            parse_file(index, path, root_dir, compile_args, nodes, call_edges)
        except Exception as e:
            print(f"✗ Failed: {e}")

    return list(nodes.values())


def generate_word_document(data, doc_name):
    """Generate Word document"""
    if not data:
        return

    doc = Document()

    for index, item in enumerate(data, start=1):
        doc.add_heading(f"1.1.{index} {item['name']}", level=1)

        table = doc.add_table(rows=3, cols=2, style="Table Grid")
        
        table.rows[0].cells[0].text = "Requirement ID"
        table.rows[0].cells[1].text = f"SAVV8-SwU-{index}"

        if item.get("description"):
            table.rows[1].cells[0].text = "Description"
            table.rows[1].cells[1].text = item["description"]

        table.rows[2].cells[0].text = "Flowchart"

        if item.get("img") and os.path.exists(item["img"]):
            try:
                table.rows[2].cells[1].add_paragraph().add_run().add_picture(
                    item["img"], width=Inches(6.0)
                )
            except Exception as e:
                print(f"  ⚠ Image error: {e}")
                table.rows[2].cells[1].text = f"Image error: {str(e)[:100]}"
        else:
            table.rows[2].cells[1].text = f"Not available: {item.get('feedback', 'Unknown')}"

    try:
        doc.save(doc_name)
        print(f"  ✓ Document saved")
    except Exception as e:
        print(f"  ✗ Save error: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="C++ Function Flowchart Generator")
    parser.add_argument("path", help="C++ codebase directory or file")
    parser.add_argument("--std", default="c++17", help="C++ standard (default: c++17)")
    parser.add_argument("--libclang", help="Path to libclang library")
    args = parser.parse_args()

    if args.libclang:
        cindex.Config.set_library_file(args.libclang)

    if not os.path.exists(args.path):
        print(f"✗ Error: Path not found: {args.path}")
        exit(1)

    os.makedirs(out_dir, exist_ok=True)
    
    ast = parse_codebase(args.path, [f"-std={args.std}"])

    print(f"\n{'='*60}")
    print(f"COMPLETE")
    print(f"{'='*60}")
    print(f"Functions: {len(ast)}")
    print(f"Output: {out_dir}")
    print(f"{'='*60}\n")
