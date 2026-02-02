"""
C++ Function Flowchart Generator - Improved Version 2.0
Fixes Unicode errors, empty flowcharts, and validation issues
"""

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
llm = ChatOllama(
    model="gpt-oss", 
    temperature=0.3, 
    top_k=10, 
    top_p=0.9,
    timeout=60  # 60 second timeout for LLM responses
)

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
    # Remove non-ASCII characters
    ascii_text = text.encode('ascii', 'ignore').decode('ascii')
    # Remove any remaining problematic characters
    ascii_text = re.sub(r'[^\x20-\x7E\n\r\t]', '', ascii_text)
    return ascii_text


def extract_flowchart_from_response(response_content):
    """Extract flowchart code from LLM response, handling various formats"""
    if not response_content:
        return ""
        
    response_content = response_content.strip()
    
    # Case 1: Code block with mermaid tag
    if "```mermaid" in response_content:
        parts = response_content.split("```mermaid")
        if len(parts) > 1:
            flowchart = parts[1].split("```")[0].strip()
            return flowchart
    
    # Case 2: Code block without language tag
    if "```" in response_content:
        parts = response_content.split("```")
        for part in parts:
            part_lower = part.lower().strip()
            if "flowchart" in part_lower:
                return part.strip()
    
    # Case 3: Direct flowchart content (no code blocks)
    if "flowchart" in response_content.lower():
        lines = response_content.split("\n")
        flowchart_lines = []
        started = False
        for line in lines:
            line_stripped = line.strip()
            if "flowchart" in line.lower() and ("td" in line.lower() or "lr" in line.lower()):
                started = True
            
            if started:
                # Stop if we hit explanatory text
                if line_stripped and not any(keyword in line_stripped for keyword in ['-->', '--', '---', '|', '[', '{', '((', '))', 'flowchart']):
                    # Check if it looks like explanation text (not node definition)
                    if re.match(r'^[A-Z][a-z]+[:\.]', line_stripped) or line_stripped.startswith(('This', 'The', 'Note', 'Explanation', '* ', '- ')):
                        break
                flowchart_lines.append(line)
        
        if flowchart_lines:
            return "\n".join(flowchart_lines).strip()
    
    return response_content.strip()


def replace_brackets_in_brackets(text):
    """Replace [, ], {, } with ASCII codes if they are inside bracket context"""
    result = []
    depth = 0
    i = 0

    while i < len(text):
        char = text[i]

        if char == "[" or char == "{":
            if depth == 0:
                result.append(char)
            else:
                if char == "[":
                    result.append("&#91;")
                elif char == "{":
                    result.append("&#123;")
            depth += 1

        elif char == "]" or char == "}":
            depth -= 1
            if depth < 0:
                depth = 0

            if depth == 0:
                result.append(char)
            else:
                if char == "]":
                    result.append("&#93;")
                elif char == "}":
                    result.append("&#125;")
        else:
            result.append(char)

        i += 1

    return "".join(result)


def sanitize_flowchart_content(flowchart_content):
    """Sanitize flowchart content to be Mermaid-compatible"""
    if not flowchart_content:
        return ""
    
    # First, clean unicode characters
    flowchart_content = clean_unicode_chars(flowchart_content)
    
    if not flowchart_content:
        return ""
    
    flowchart_list = flowchart_content.split("\n")
    new_list = []
    flowchart_started = False

    for line in flowchart_list:
        line_lower = line.lower().strip()
        
        # Start capturing when we see flowchart declaration
        if ("flowchart td" in line_lower or "flowchart lr" in line_lower) and not flowchart_started:
            flowchart_started = True
            # Normalize to flowchart TD
            if "flowchart lr" in line_lower:
                new_list.append("flowchart LR")
            else:
                new_list.append("flowchart TD")
            continue

        if not flowchart_started:
            continue

        # Skip empty lines
        if not line.strip():
            continue
        
        # Stop if we hit explanatory text or markdown
        if line.strip().startswith(('```', 'Note:', 'Explanation:', '##', '# ', '---')):
            break

        # Process the line
        original_line = line.strip()
        
        # Skip if it's another flowchart declaration
        if "flowchart" in original_line.lower():
            continue
            
        # Process the line for Mermaid compatibility
        processed_line = original_line
        
        # Preserve Start((Start)) and End((End)) patterns
        has_start = "((Start))" in processed_line
        has_end = "((End))" in processed_line
        
        # Replace parentheses except in special patterns
        if not has_start and not has_end:
            # Replace parentheses in labels but not in arrows
            temp_line = ""
            in_arrow = False
            i = 0
            while i < len(processed_line):
                if i < len(processed_line) - 1:
                    two_char = processed_line[i:i+2]
                    if two_char in ["--", "->", "=>", "-.","=="]:
                        in_arrow = True
                        temp_line += two_char
                        i += 2
                        continue
                
                if processed_line[i] == " " and in_arrow:
                    in_arrow = False
                
                if not in_arrow:
                    if processed_line[i] == "(":
                        temp_line += "&#40;"
                    elif processed_line[i] == ")":
                        temp_line += "&#41;"
                    else:
                        temp_line += processed_line[i]
                else:
                    temp_line += processed_line[i]
                i += 1
            processed_line = temp_line
        
        # Restore Start and End if they were there
        processed_line = processed_line.replace("&#40;&#40;Start&#41;&#41;", "((Start))")
        processed_line = processed_line.replace("&#40;&#40;End&#41;&#41;", "((End))")
        
        # Handle brackets in labels
        processed_line = replace_brackets_in_brackets(processed_line)
        
        # Replace comparison operators in decision nodes
        if "{{" in processed_line and "}}" in processed_line:
            start_idx = processed_line.find("{{")
            end_idx = processed_line.find("}}", start_idx)
            if start_idx != -1 and end_idx != -1:
                condition = processed_line[start_idx+2:end_idx]
                condition = (
                    condition.replace("!=", " not equal ")
                    .replace("==", " equal ")
                    .replace(">=", " gte ")
                    .replace("<=", " lte ")
                    .replace(">", " gt ")
                    .replace("<", " lt ")
                    .replace("&&", " and ")
                    .replace("||", " or ")
                )
                processed_line = processed_line[:start_idx+2] + condition + processed_line[end_idx:]
        
        new_list.append(processed_line)

    result = "\n".join(new_list)
    return result if result.strip() else ""


def validate_mermaid_syntax(mermaid_content):
    """Validate Mermaid syntax - Returns (is_valid, error_message)"""
    if not mermaid_content or len(mermaid_content.strip()) == 0:
        return False, "Empty flowchart content"

    if "flowchart" not in mermaid_content.lower():
        return False, "Missing flowchart declaration"

    if "Start" not in mermaid_content and "start" not in mermaid_content:
        return False, "Missing Start node"
    
    if "End" not in mermaid_content and "end" not in mermaid_content.lower():
        return False, "Missing End node"

    if "-->" not in mermaid_content and "--" not in mermaid_content:
        return False, "No connections found in flowchart"

    lines = [l.strip() for l in mermaid_content.split("\n") if l.strip()]
    if len(lines) < 3:
        return False, f"Too few lines in flowchart: {len(lines)}"

    # Check for unlabeled nodes - improved logic
    import re
    defined_nodes = set()
    used_nodes = set()
    
    for line in lines:
        if "flowchart" in line.lower():
            continue
            
        # Find node definitions (nodes with labels)
        # Pattern: n1[...] or n2{{...}} or Start((...)) or End((...))
        defs = re.findall(r'\b(n\d+|Start|End)[\[\{(]', line)
        defined_nodes.update(defs)
        
        # Find node references in arrows
        # Pattern: --> n1 or n1 --> (but not n1[)
        refs = re.findall(r'(?:-->|--)\s*(n\d+)\s*(?:$|-->|--|\|)', line)
        used_nodes.update(refs)
    
    # Find nodes that are used but not defined
    unlabeled = used_nodes - defined_nodes
    unlabeled = [n for n in unlabeled if n not in ['Start', 'End']]
    
    if unlabeled:
        return False, f"Nodes used without labels: {', '.join(sorted(unlabeled)[:3])}. Each node must be defined with a label like n1[Description]"

    return True, None


def extract_function_calls(function_content):
    """Extract function calls from the function content"""
    function_calls = []
    pattern = r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\('
    matches = re.findall(pattern, "\n".join(function_content))
    
    keywords = {
        'if', 'while', 'for', 'switch', 'return', 'new', 'delete', 'sizeof',
        'static_cast', 'dynamic_cast', 'const_cast', 'reinterpret_cast',
        'printf', 'sprintf', 'fprintf', 'assert', 'memset', 'memcpy', 'free', 'malloc'
    }
    
    seen = set()
    for match in matches:
        if match not in keywords and match not in seen:
            function_calls.append(match)
            seen.add(match)
    
    return function_calls


def generate_function_description(function_content):
    """Generate description for a function using LLM"""
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
        description_response = llm.invoke(messages)
        return description_response.content.strip()
    except Exception as e:
        print(f"Error generating description: {e}")
        return "Description generation failed"


def get_short_prompt(function_content, function_calls_str):
    """Shorter prompt for complex/long functions"""
    return (
        "Create a Mermaid flowchart for this C++ function.\n\n"
        "RULES:\n"
        "1. Start with: flowchart TD\n"
        "2. Begin: Start((Start))\n"
        "3. End: End((End))\n"
        "4. Every node needs a label: n1[Action description] or n2{{Condition}}\n"
        "5. NO unlabeled nodes like 'n1 -->' - must be 'n1[Description] -->'\n"
        f"6. Function calls: {function_calls_str}\n\n"
        "Format:\n"
        "flowchart TD\n"
        "Start((Start)) --> n1[First action]\n"
        "n1 --> n2{{Check condition}}\n"
        "n2 --> |true| n3[True action]\n"
        "n2 --> |false| n4[False action]\n"
        "n3 --> End((End))\n"
        "n4 --> End((End))\n\n"
        "Function:\n{function}\n\n"
        "Output flowchart only:"
    )


def generate_flowchart(function_content, function_name):
    """Generate flowchart for a function with validation and retry mechanism"""
    function_calls = extract_function_calls(function_content)
    function_calls_str = ", ".join(function_calls[:5]) if function_calls else "none"
    
    # Limit function content for very long functions
    max_lines = 100
    if len(function_content) > max_lines:
        print(f"  Note: Function has {len(function_content)} lines, using first {max_lines} for analysis")
        function_content_limited = function_content[:max_lines]
    else:
        function_content_limited = function_content
    
    flowchart_prompt = (
        "Create a Mermaid flowchart for this C++ function.\n\n"
        "CRITICAL RULES:\n"
        "1. Output format: flowchart TD\n"
        "2. Start node: Start((Start))\n"
        "3. End node: End((End))\n"
        "4. EVERY node MUST have a label:\n"
        "   - Process: n1[Action description]\n"
        "   - Decision: n2{{Condition}}\n"
        "5. NO unlabeled nodes (n1 --> is WRONG, must be n1[Label] -->)\n"
        f"6. Function calls: {function_calls_str}\n\n"
        "Map control flow:\n"
        "- if/else: n1{{Condition}} --> |true| n2[Action] / |false| n3[Else action]\n"
        "- loops: n1[Init] --> n2{{Loop condition}} --> |true| n3[Body] --> n2 / |false| n4[After]\n"
        "- switch: n1{{Switch var}} --> |case1| n2[Action1] / |case2| n3[Action2]\n"
        "- return: n1[Return value] --> End((End))\n\n"
        "Example:\n"
        "flowchart TD\n"
        "Start((Start)) --> n1[Initialize]\n"
        "n1 --> n2{{Check condition}}\n"
        "n2 --> |true| n3[Do action]\n"
        "n2 --> |false| End((End))\n"
        "n3 --> End((End))\n\n"
        "Function:\n{function}\n\n"
        "Output (flowchart only, ASCII, all nodes labeled):"
    )

    print(f"\nGenerating flowchart for function: {function_name} ({len(function_content)} lines)")
    query = flowchart_prompt.format(function="\n".join(function_content_limited))
    
    retries = 0
    max_retries = 5
    last_error = None
    use_short_prompt = False

    while retries < max_retries:
        try:
            # After 2 failed attempts with main prompt, try shorter prompt
            if retries == 2 and not use_short_prompt:
                print(f"  Switching to simplified prompt...")
                query = get_short_prompt(function_content_limited, function_calls_str).format(
                    function="\n".join(function_content_limited[:60])  # Even more limited
                )
                use_short_prompt = True
            
            messages = [HumanMessage(query)]
            flowchart_response = llm.invoke(messages)
            raw_content = flowchart_response.content
            
            if not raw_content or len(raw_content.strip()) == 0:
                last_error = "LLM returned empty response"
                print(f"Attempt {retries + 1}: {last_error}")
                # Try reducing function size further
                if len(function_content_limited) > 30:
                    function_content_limited = function_content_limited[:len(function_content_limited)//2]
                    query = (get_short_prompt(function_content_limited, function_calls_str) if use_short_prompt 
                            else flowchart_prompt).format(function="\n".join(function_content_limited))
                    print(f"  Reducing function size to {len(function_content_limited)} lines")
                retries += 1
                continue

            extracted_content = extract_flowchart_from_response(raw_content)
            
            if not extracted_content or len(extracted_content.strip()) == 0:
                last_error = "Could not extract flowchart from LLM response"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                continue

            sanitized_content = sanitize_flowchart_content(extracted_content)

            if not sanitized_content or len(sanitized_content.strip()) == 0:
                last_error = "Flowchart became empty after sanitization"
                print(f"Attempt {retries + 1}: {last_error}")
                print(f"  Raw length: {len(raw_content)}, Extracted length: {len(extracted_content)}")
                print(f"  Extracted content preview: {extracted_content[:200]}")
                # If extraction worked but sanitization failed, try with minimal sanitization
                if len(extracted_content) > 20:
                    # Try just cleaning unicode without full sanitization
                    sanitized_content = clean_unicode_chars(extracted_content)
                    if not sanitized_content or len(sanitized_content.strip()) == 0:
                        retries += 1
                        continue
                else:
                    retries += 1
                    continue

            is_valid, error_msg = validate_mermaid_syntax(sanitized_content)
            
            if not is_valid:
                last_error = f"Validation failed: {error_msg}"
                print(f"Attempt {retries + 1}: {last_error}")
                retries += 1
                query = flowchart_prompt + f"\n\nPREVIOUS ATTEMPT FAILED: {error_msg}\nPlease fix this issue."
                continue

            # Try to generate image
            currdir = os.getcwd()
            try:
                os.chdir(mermaid_path)

                out = subprocess.check_output(
                    ["node", "index.js", sanitized_content, function_name + ".png"],
                    stderr=subprocess.STDOUT,
                    timeout=30
                )
                output_str = str(out.lower())
                
                os.chdir(currdir)

                if "error" not in output_str and "failed" not in output_str:
                    img_path = os.path.join(mermaid_path, function_name + ".png")
                    if os.path.exists(img_path):
                        print(f"✓ Flowchart generated successfully")
                        return sanitized_content, img_path, "Success"
                    else:
                        last_error = "Image file not created"
                        print(f"Attempt {retries + 1}: {last_error}")
                else:
                    error_match = re.search(r'error[:\s]+([^\n]+)', output_str)
                    if error_match:
                        last_error = f"Mermaid error: {error_match.group(1)[:100]}"
                    else:
                        last_error = f"Mermaid conversion error: {output_str[:200]}"
                    print(f"Attempt {retries + 1}: {last_error}")
                    
            except subprocess.TimeoutExpired:
                os.chdir(currdir)
                last_error = "Mermaid conversion timeout"
                print(f"Attempt {retries + 1}: {last_error}")
            except subprocess.CalledProcessError as e:
                os.chdir(currdir)
                last_error = f"Mermaid process error: {str(e)[:100]}"
                print(f"Attempt {retries + 1}: {last_error}")
            except FileNotFoundError:
                os.chdir(currdir)
                last_error = "Mermaid converter not found - check mermaid_path configuration"
                print(f"Attempt {retries + 1}: {last_error}")
                break
            except Exception as e:
                os.chdir(currdir)
                last_error = f"Image generation error: {str(e)[:100]}"
                print(f"Attempt {retries + 1}: {last_error}")

            retries += 1
            if retries < max_retries:
                query = flowchart_prompt + f"\n\nPREVIOUS ATTEMPT FAILED: {last_error}\nPlease fix and regenerate."

        except Exception as e:
            print(f"Exception during flowchart generation attempt {retries + 1}: {e}")
            last_error = f"Exception: {str(e)[:100]}"
            retries += 1

    print(f"✗ Failed to generate valid flowchart after {max_retries} attempts")
    print(f"  Last error: {last_error}")
    return None, None, last_error or "Failed after all retries"


def extract_node_info(cursor, file_path, module_name):
    """Extract information about a function node from AST"""
    extent = cursor.extent
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        start_line = max(0, extent.start.line - 1)
        end_line = min(len(lines), extent.end.line)
        function_lines = lines[start_line:end_line]
        function_lines = [line.rstrip() for line in function_lines if line.strip()]

        if not function_lines:
            print(f"Warning: No content extracted for function {cursor.spelling}")
            return None

        print(f"\nProcessing function: {cursor.spelling} ({len(function_lines)} lines)")

        flowchart_content, flowchart_image_path, flowchart_feedback = generate_flowchart(
            function_lines, cursor.spelling
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
            "flowchart": flowchart_content,
            "feedback": flowchart_feedback,
            "img": flowchart_image_path,
            "callees": [],
            "callers": [],
        }
    except Exception as e:
        print(f"Error extracting node info for {cursor.spelling}: {e}")
        import traceback
        traceback.print_exc()
        return None


def visit(cursor, file_path, module_name, nodes, call_edges, current_fn, visited=None):
    """Recursively visit AST nodes to extract functions and call relationships"""
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
    """Parse a single C++ file and extract function information"""
    module_name = get_module_name(file_path, root_dir)

    try:
        print(f"\n{'='*60}")
        print(f"Parsing file: {os.path.basename(file_path)}")
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
            print(f"⚠ No functions found in {file_path}")
            return

        file_name = os.path.splitext(os.path.basename(file_path))[0]
        meta_file_name = file_name

        if file_name in file_index_map:
            meta_file_name = f"{file_name}_{file_index_map[file_name]}"
            file_index_map[file_name] += 1
        else:
            file_index_map[file_name] = 1

        meta_file_name += ".json"
        file_name_docx = file_name + ".docx"

        print(f"\nGenerating outputs:")
        print(f"  - Word Document: {file_name_docx}")
        print(f"  - JSON Metadata: {meta_file_name}")

        generate_word_document(list(my_nodes.values()), os.path.join(out_dir, file_name_docx))

        with open(os.path.join(out_dir, meta_file_name), "w", encoding='utf-8') as f:
            json.dump(list(my_nodes.values()), f, indent=2, ensure_ascii=False)

        print(f"✓ File processing complete: {len(my_nodes)} functions")

        nodes.update(my_nodes)
        call_edges.update(my_call_edges)

    except Exception as e:
        print(f"\n✗ ERROR parsing {file_path}:")
        print(f"  {str(e)}")
        import traceback
        traceback.print_exc()


def parse_codebase(root_dir, compile_args=None):
    """Parse entire C++ codebase and extract all functions"""
    compile_args = compile_args or ["-std=c++17"]
    index = cindex.Index.create()

    nodes = {}
    call_edges = defaultdict(set)

    cpp_files = []
    for root, _, files in os.walk(root_dir):
        for f in files:
            if is_cpp_file(f):
                path = os.path.join(root, f)
                cpp_files.append(path)

    print(f"\n{'='*60}")
    print(f"CODEBASE ANALYSIS STARTED")
    print(f"{'='*60}")
    print(f"Root directory: {root_dir}")
    print(f"Found {len(cpp_files)} C++ files to process")
    print(f"Output directory: {out_dir}")
    print(f"{'='*60}\n")

    for idx, path in enumerate(cpp_files, 1):
        print(f"\n[{idx}/{len(cpp_files)}] Processing: {os.path.relpath(path, root_dir)}")
        try:
            parse_file(index, path, root_dir, compile_args, nodes, call_edges)
        except Exception as e:
            print(f"✗ Failed to parse {path}: {e}")

    return list(nodes.values())


def generate_word_document(data, doc_name):
    """Generate Word document with function flowcharts"""
    if not data:
        print("⚠ No data to generate document")
        return

    doc = Document()

    for index, item in enumerate(data, start=1):
        heading = f"1.1.{index} {item['name']}"
        doc.add_heading(heading, level=1)

        table = doc.add_table(rows=3, cols=2, style="Table Grid")
        
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "Requirement ID"
        hdr_cells[1].text = f"SAVV8-SwU-{index}"

        if item.get("description"):
            desc_cells = table.rows[1].cells
            desc_cells[0].text = "Description"
            desc_cells[1].text = item["description"]

        flow_cells = table.rows[2].cells
        flow_cells[0].text = "Flowchart"

        if item.get("img") and item["img"] and os.path.exists(item["img"]):
            try:
                flow_cells[1].add_paragraph().add_run().add_picture(
                    item["img"], width=Inches(6.0)
                )
            except Exception as e:
                print(f"  ⚠ Error adding image for {item['name']}: {e}")
                flow_cells[1].text = f"Flowchart image error: {str(e)[:100]}"
        else:
            error_msg = item.get('feedback', 'Flowchart generation failed')
            flow_cells[1].text = f"Flowchart not available: {error_msg}"

    try:
        doc.save(doc_name)
        print(f"  ✓ Document saved: {doc_name}")
    except Exception as e:
        print(f"  ✗ Error saving document {doc_name}: {e}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate flowcharts for C++ functions using LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python code.py /path/to/cpp/project
  python code.py /path/to/cpp/project --std c++20
  python code.py D:\\git-project\\poseidonos\\src\\memory_checker
        """
    )
    parser.add_argument("path", help="C++ codebase root directory or specific file")
    parser.add_argument("--std", default="c++17", help="C++ standard (default: c++17)")
    parser.add_argument("--libclang", help="Path to libclang library (optional)")
    args = parser.parse_args()

    if args.libclang:
        cindex.Config.set_library_file(args.libclang)

    if not os.path.exists(args.path):
        print(f"✗ Error: Path does not exist: {args.path}")
        exit(1)

    os.makedirs(out_dir, exist_ok=True)
    
    compile_args = [f"-std={args.std}"]
    
    ast = parse_codebase(args.path, compile_args=compile_args)

    print(f"\n{'='*60}")
    print(f"PROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Total functions processed: {len(ast)}")
    print(f"Output directory: {out_dir}")
    print(f"{'='*60}\n")
