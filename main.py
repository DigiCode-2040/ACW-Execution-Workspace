"""ACW desktop launcher. No network listener is created."""
from pathlib import Path
import webview
from acw.api import API

if __name__ == '__main__':
    index = (Path(__file__).parent / 'ui' / 'index.html').resolve()
    webview.create_window('ACW · Execution Workspace', index.as_uri(), js_api=API(), width=1440, height=900, min_size=(900, 600))
    webview.start(debug=False)
