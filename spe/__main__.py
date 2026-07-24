"""Module entry-point: ``python -m spe`` runs the API server."""
from __future__ import annotations

import uvicorn


def main() -> None:  # pragma: no cover
    uvicorn.run("spe.app:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
