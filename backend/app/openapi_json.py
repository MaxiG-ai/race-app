"""Print the OpenAPI schema to stdout.

The frontend's types are generated from this rather than hand-written (§5), and
generating them shouldn't require a running server — `npm run gen:api` shells out
to `python -m app.openapi_json` and pipes the result into openapi-typescript.
"""

import json

from app.main import app


def main() -> None:
    print(json.dumps(app.openapi(), indent=2))


if __name__ == "__main__":
    main()
