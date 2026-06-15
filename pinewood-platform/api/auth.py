"""Authentication & authorization for the Pinewood API.

Scheme: signed JWT bearer tokens (HS256). We chose JWT over plain API keys
because the role + scope (region / community) travel inside the signed token,
so authorization can be enforced server-side from the token claims alone
without a second lookup — and the token cannot be tampered with client-side.

Roles:
  corporate_admin   -> sees everything
  regional_director -> sees only communities in `region`
  community_ed      -> sees only `community_id`

Authorization is enforced in the route layer via `authorize_communities`,
which intersects the caller's allowed community set with any requested filter.
Clients can never widen their own scope.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import duckdb
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Dev secret. In production this comes from a secret manager / env var.
SECRET_KEY = os.environ.get("PINEWOOD_JWT_SECRET", "pinewood-dev-secret-change-me")
ALGORITHM = "HS256"

bearer_scheme = HTTPBearer(auto_error=False)


def create_token(sub: str, role: str, region: str | None = None,
                 community_id: str | None = None, days: int = 365) -> str:
    payload = {
        "sub": sub,
        "role": role,
        "region": region,
        "community_id": community_id,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=days),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")


def get_current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """Reject any request without a valid bearer token."""
    if creds is None or not creds.credentials:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_token(creds.credentials)
    if claims.get("role") not in ("corporate_admin", "regional_director", "community_ed"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unknown role")
    return claims


def allowed_communities(con: duckdb.DuckDBPyConnection, principal: dict) -> set[str] | None:
    """Return the set of community_ids this principal may see.

    None means "all communities" (corporate admin). The set is computed
    server-side from dim_community, never from client input.
    """
    role = principal["role"]
    if role == "corporate_admin":
        return None
    if role == "regional_director":
        region = principal.get("region")
        rows = con.execute(
            "SELECT community_id FROM gold.dim_community WHERE region = ?",
            [region],
        ).fetchall()
        return {r[0] for r in rows}
    if role == "community_ed":
        cid = principal.get("community_id")
        return {cid} if cid else set()
    return set()


def authorize_communities(
    con, principal: dict, requested_community_id: str | None = None,
    requested_region: str | None = None,
) -> list[str]:
    """Resolve the final list of community_ids a query may touch.

    Intersects the caller's allowed scope with any community/region filter the
    caller asked for. If the caller requests something outside their scope we
    return 403 rather than silently widening or narrowing.
    """
    allowed = allowed_communities(con, principal)

    requested: set[str] | None = None
    if requested_region:
        rows = con.execute(
            "SELECT community_id FROM gold.dim_community WHERE region = ?",
            [requested_region],
        ).fetchall()
        requested = {r[0] for r in rows}
    if requested_community_id:
        rc = {requested_community_id}
        requested = rc if requested is None else (requested & rc)

    if allowed is None:                 # corporate admin
        final = requested if requested is not None else set(
            r[0] for r in con.execute(
                "SELECT community_id FROM gold.dim_community"
            ).fetchall()
        )
    else:
        if requested is None:
            final = allowed
        else:
            outside = requested - allowed
            if outside:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Not authorized for communities: {sorted(outside)}",
                )
            final = requested & allowed
    return sorted(final)
