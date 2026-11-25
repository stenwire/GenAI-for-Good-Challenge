# municipal-qa

## Notebooks

Notebooks live in `notebooks/` and import from the package (`from municipal_qa.config import load_config`); the package never imports a notebook. In VS Code select the `.venv` kernel.

Strip outputs before committing. Run once per clone:

```bash
uv run nbstripout --install
```
