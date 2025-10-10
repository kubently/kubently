# Kubently Comprehensive Test Report

**Generated:** 2025-09-09T16:42:18.599475

## Executive Summary

- **Total Scenarios Tested:** 20
- **Successful:** 8
- **Failed:** 12
- **Success Rate:** 40.0%
- **Average Debug Duration:** 20.53 seconds
- **Total Tool Calls:** 0

## Common Bottlenecks


## Top Recommendations

1. Add to system prompt: 'For debugging requests, immediately use kubectl commands to investigate - do not ask for permission or more information' (suggested 17 times)
1. Fix scenario setup script (suggested 3 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-01), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-02), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-03), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-04), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-05), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-06), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-07), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-08), use it in all kubectl commands' (suggested 1 times)

## Detailed Scenario Results

### 01-imagepullbackoff-typo

- **Success:** ✓
- **Duration:** 8.62s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 02-imagepullbackoff-private

- **Success:** ✓
- **Duration:** 4.66s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 03-crashloopbackoff

- **Success:** ✓
- **Duration:** 16.91s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 04-runcontainer-missing-configmap

- **Success:** ✓
- **Duration:** 13.21s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 05-runcontainer-missing-secret-key

- **Success:** ✓
- **Duration:** 12.61s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 06-oomkilled

- **Success:** ✓
- **Duration:** 16.65s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 07-failed-readiness-probe

- **Success:** ✓
- **Duration:** 22.12s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 08-failing-liveness-probe

- **Success:** ✓
- **Duration:** 24.97s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 09-mismatched-labels

- **Success:** ✗
- **Duration:** 21.89s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 10-unschedulable-resources

- **Success:** ✗
- **Duration:** 120.02s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 11-unschedulable-taint

- **Success:** ✗
- **Duration:** 30.03s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 12-pvc-unbound

- **Success:** ✗
- **Duration:** 30.06s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 13-service-selector-mismatch

- **Success:** ✗
- **Duration:** 30.03s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 14-service-port-mismatch

- **Success:** ✗
- **Duration:** 13.75s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 15-network-policy-deny-ingress

- **Success:** ✗

### 16-network-policy-deny-egress

- **Success:** ✗

### 17-cross-namespace-block

- **Success:** ✗
- **Duration:** 15.01s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 18-missing-serviceaccount

- **Success:** ✗

### 19-rbac-forbidden-role

- **Success:** ✗
- **Duration:** 15.02s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 20-rbac-forbidden-clusterrole

- **Success:** ✗
- **Duration:** 15.01s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

