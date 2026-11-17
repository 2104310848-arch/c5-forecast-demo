const runtimeUrl = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
let runtime;
async function initialize() {
  importScripts(runtimeUrl + 'pyodide.js');
  const python = await loadPyodide({indexURL: runtimeUrl});
  await python.loadPackage(['numpy', 'pandas']);
  const model = await fetch('model.py');
  if (!model.ok) throw Error('æ— æ³•åŠ è½½é¢„æµ‹æ¨¡åž‹ã€‚');
  python.FS.writeFile('/model.py', await model.text());
  return python;
}
self.onmessage = async ({data}) => {
  try {
    runtime ??= initialize().catch(error => { runtime = undefined; throw error; });
    const python = await runtime;
    const scope = python.toPy(new Map(Object.entries(data)));
    try {
      const result = await python.runPythonAsync(`
from pathlib import Path
import tempfile, zipfile, base64, json, sys
if '/' not in sys.path: sys.path.insert(0, '/')
import pandas as pd
from model import run, save
import io
features, backtest, forecast, summary, alerts = run(
    pd.read_csv(io.StringIO(daily)), pd.read_csv(io.StringIO(events)), target
)
with tempfile.TemporaryDirectory() as directory:
    save((features, backtest, forecast, summary, alerts), directory)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(Path(directory).iterdir()):
            archive.write(file, file.name)
json.dumps({
    "summary": summary,
    "backtest": json.loads(backtest.to_json(orient="records")),
    "forecast": json.loads(forecast[["date", "point"]].to_json(orient="records")),
    "zip": base64.b64encode(buffer.getvalue()).decode()
}, ensure_ascii=False, allow_nan=False)
`, {globals: scope});
      self.postMessage({result: JSON.parse(result)});
    } finally { scope.destroy(); }
  } catch (error) {
    self.postMessage({error: String(error.message || error).trim().split('\n').at(-1)});
  }
};
