# API contracts

`openapi.yaml` is the versioned HTTP contract. The files in `schemas/` are
JSON Schema 2020-12 documents referenced by OpenAPI and suitable for generating
component-local models.

Runtime components must copy or generate models during their own build. They
must not import source from another component. A contract change therefore
triggers both Pi and backend CI.

Validate the files locally without creating repository output:

```bash
python -c "import json,pathlib; [json.load(p.open()) for p in pathlib.Path('contracts/schemas').glob('*.json')]"
```
