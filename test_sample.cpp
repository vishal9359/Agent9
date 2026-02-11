// Test file to verify flowchart generation improvements
#include <iostream>
#include <vector>

class QoS {
public:
    void GetVolume(int vol) {
        std::cout << "Getting volume: " << vol << std::endl;
    }
};

int volPolicy[10];

// Test function with various C++ constructs
int testFunction(int volId, bool bw, bool volPolicyInEffect) {
    QoS* qos = new QoS();
    
    // Test 1: Function call with parameters
    qos->GetVolume(volId);
    
    // Test 2: Return with expression
    if (volId < 0) {
        return volPolicy[volId];
    }
    
    // Test 3: Multiple assignments
    bool minBW = bw;
    bool minPolicy = volPolicyInEffect;
    
    // Test 4: If condition with comparison
    if (0 == minPolicy) {
        minBW = false;
    }
    
    // Test 5: Simple return
    return 0;
}

void uniqueLock(int policy) {
    std::cout << "Lock acquired with policy: " << policy << std::endl;
}

int main() {
    testFunction(5, true, false);
    uniqueLock(1);
    return 0;
}
