# Kubently Comprehensive Test Report

**Generated:** 2025-09-11T15:51:20.443986

## Executive Summary

- **Total Scenarios Tested:** 20
- **Successful:** 13
- **Failed:** 7
- **Success Rate:** 65.0%
- **Average Debug Duration:** 10.55 seconds
- **Total Tool Calls:** 0

## Common Bottlenecks


## Top Recommendations

1. Add to system prompt: 'For debugging requests, immediately use kubectl commands to investigate - do not ask for permission or more information' (suggested 19 times)
1. Add to system prompt: 'Focus on diagnosis and recommendations without mentioning read-only limitations' (suggested 5 times)
1. Add to system prompt: 'When a namespace is provided (e.g., test-scenario-XX), use it directly without asking for confirmation' (suggested 2 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-01), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-02), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-03), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-04), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-05), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-06), use it in all kubectl commands' (suggested 1 times)
1. Add to system prompt: 'When namespace is explicitly mentioned in the query (like test-scenario-07), use it in all kubectl commands' (suggested 1 times)

## Detailed Scenario Results

### 01-imagepullbackoff-typo

- **Success:** ✓
- **Duration:** 7.76s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 02-imagepullbackoff-private

- **Success:** ✓
- **Duration:** 14.17s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 03-crashloopbackoff

- **Success:** ✓
- **Duration:** 9.38s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 04-runcontainer-missing-configmap

- **Success:** ✓
- **Duration:** 11.43s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 05-runcontainer-missing-secret-key

- **Success:** ✗
- **Duration:** 1.41s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 06-oomkilled

- **Success:** ✗
- **Duration:** 13.96s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 07-failed-readiness-probe

- **Success:** ✓
- **Duration:** 10.23s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 08-failing-liveness-probe

- **Success:** ✓
- **Duration:** 8.72s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 09-mismatched-labels

- **Success:** ✗
- **Duration:** 14.11s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 10-unschedulable-resources

- **Success:** ✓
- **Duration:** 7.13s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 11-unschedulable-taint

- **Success:** ✓
- **Duration:** 6.35s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 12-pvc-unbound

- **Success:** ✗
- **Duration:** 1.34s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 13-service-selector-mismatch

- **Success:** ✓
- **Duration:** 20.30s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 14-service-port-mismatch

- **Success:** ✓
- **Duration:** 26.90s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 15-network-policy-deny-ingress

- **Success:** ✓
- **Duration:** 15.69s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 16-network-policy-deny-egress

- **Success:** ✗
- **Duration:** 9.18s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 17-cross-namespace-block

- **Success:** ✓
- **Duration:** 13.81s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 18-missing-serviceaccount

- **Success:** ✗

### 19-rbac-forbidden-role

- **Success:** ✓
- **Duration:** 9.60s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 7.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

### 20-rbac-forbidden-clusterrole

- **Success:** ✗
- **Duration:** 9.50s
- **Tool Calls:** 0
- **Tokens:** 0
- **Quality Score:** 3.0/10
- **Quality Justification:** Heuristic scoring based on root cause detection

