"""ACW desktop launcher. No network listener is created."""
from pathlib import Path
import webview
from acw.api import API

if __name__ == '__main__':
    index = (Path(__file__).parent / 'ui' / 'index.html').resolve()
    api = API()
    webview.create_window('ACW · Execution Workspace', index.as_uri(), js_api=api, width=1100, height=900, min_size=(900, 600))
    try:
        api.engine.automation.start()
        webview.start(debug=False)
    finally:
        api.shutdown()
