from pathlib import Path
import hashlib,pandas as pd
root=Path('artifacts/model-deadline-20261027T1634Z')
a=(root/'baseline-rates-run-1.csv').read_bytes();b=(root/'baseline-rates-run-2.csv').read_bytes()
print({'rows':len(pd.read_csv(root/'baseline-rates-run-1.csv')),'run1_sha256':hashlib.sha256(a).hexdigest(),'run2_sha256':hashlib.sha256(b).hexdigest(),'byte_identical':a==b})
