# ACW_COMMAND: quality
# ACW_LABEL: Quality Check
# ACW_DESCRIPTION: Illustrative validation task
import json
import time
for stage, pct in [('Discover', 20), ('Test', 60), ('Report', 100)]:
    print('ACW_EVENT:' + json.dumps({'stage': stage, 'progress': pct}), flush=True)
    print(f'{stage}: checks passed', flush=True)
    time.sleep(1.4)
print('ACW_EVENT:' + json.dumps({'quality': 100, 'stage': 'Complete', 'progress': 100}), flush=True)
