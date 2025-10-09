#!/usr/bin/env python3
"""Fix all scenario files by ensuring EOF markers are not indented."""

import re
from pathlib import Path

def fix_scenario_file(file_path: Path):
    """Fix EOF indentation in a scenario file."""
    content = file_path.read_text()
    
    # Find all occurrences of indented EOF
    lines = content.split('\n')
    fixed_lines = []
    
    for i, line in enumerate(lines):
        # If line is just spaces + EOF, remove the spaces
        if line.strip() == 'EOF' and line != 'EOF':
            fixed_lines.append('EOF')
            print(f"Fixed indented EOF at line {i+1} in {file_path.name}")
        else:
            fixed_lines.append(line)
    
    # Write back
    fixed_content = '\n'.join(fixed_lines)
    file_path.write_text(fixed_content)

def main():
    """Fix all scenario files."""
    scenarios_dir = Path("scenarios")
    
    for scenario_file in sorted(scenarios_dir.glob("[0-9]*.sh")):
        if scenario_file.name in ['01-imagepullbackoff-typo.sh', '13-service-selector-mismatch.sh']:
            print(f"Skipping {scenario_file.name} (already fixed)")
            continue
        
        print(f"Fixing {scenario_file.name}...")
        fix_scenario_file(scenario_file)

if __name__ == "__main__":
    main()