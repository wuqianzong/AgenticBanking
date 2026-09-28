import json
with open('backend/logs/system.jsonl') as f:
    lines = f.readlines()
for line in lines[-200:]:
    if 'llm.response' in line:
        data = json.loads(line)
        if 'raw_response' in data and 'tool_tasks' in data['raw_response']:
            print("Response:", data['raw_response'])
