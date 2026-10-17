import json
import time

def emit(**data):
    print('ACW_EVENT:' + json.dumps(data), flush=True)

for stage, progress in [('Initialize', 10), ('Load data', 30), ('Transform', 55), ('Validate', 85), ('Complete', 100)]:
    emit(stage=stage, progress=progress)
    print(f'{stage}: processing batch', flush=True)
    time.sleep(1.1)
emit(stage='Complete', progress=100, quality=98)
print('Demo pipeline completed successfully.', flush=True)
