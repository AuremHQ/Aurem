"""Backend-served /.well-known/api-catalog (RFC 9264 linkset).

2026-11-19 - the static file that used to live at
frontend/public/.well-known/api-catalog was served by nginx as
application/octet-stream (extensionless file, and the host ignores the
frontend/public/_headers content types), which shadowed any backend
route. The catalog is now served here with Content-Type:
application/linkset+json, and the static file is removed.
"""

from fastapi.responses import JSONResponse

API_CATALOG_LINKSET = {
    "linkset": [
        {
            "anchor": "https://auremcto.com/",
            "service-desc": [
                {
                    "href": "https://auremcto.com/api/openapi.json",
                    "type": "application/vnd.oai.openapi+json;version=3.1",
                },
                {
                    "href": "https://auremcto.com/openapi.json",
                    "type": "application/vnd.oai.openapi+json;version=3.1",
                },
            ],
            "service-doc": [
                {"href": "https://auremcto.com/api/docs", "type": "text/html"},
                {"href": "https://auremcto.com/api/redoc", "type": "text/html"},
            ],
        }
    ]
}


async def api_catalog_root(_request):
    return JSONResponse(API_CATALOG_LINKSET, media_type="application/linkset+json")
