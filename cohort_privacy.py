#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import math
import numbers
import os


DEFAULT_HASH_SALT = "DM_WHTR_PROJECT_DETERMINISTIC_SALT_V1"
REQUIRE_HASH_SALT_ENV = "DM_WHTR_REQUIRE_HASH_SALT"


def _canonical_identifier(identifier):
    if isinstance(identifier, numbers.Integral):
        return str(int(identifier))
    if isinstance(identifier, numbers.Real) and math.isfinite(float(identifier)) and float(identifier).is_integer():
        return str(int(identifier))
    return str(identifier).strip()


def _env_flag_enabled(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y", "on"}


def get_hash_salt(require_env=False):
    # NHIS 운영/반출 환경에서는 재식별 위험 완화를 위해 DM_WHTR_HASH_SALT 환경변수를 반드시 주입하십시오.
    salt = os.environ.get("DM_WHTR_HASH_SALT")
    if salt:
        return salt
    if require_env or _env_flag_enabled(REQUIRE_HASH_SALT_ENV):
        raise RuntimeError("DM_WHTR_HASH_SALT must be set when operational hash salt enforcement is enabled")
    return DEFAULT_HASH_SALT


def require_operational_salt():
    return get_hash_salt(require_env=True)


def validate_unique_hashed_ids(df, id_column="ID", context="cohort"):
    if id_column not in df.columns:
        raise ValueError(f"{context} must include {id_column} before hash collision validation")

    duplicated = df[df[id_column].duplicated(keep=False)][id_column].astype(str).unique().tolist()
    if duplicated:
        examples = ", ".join(duplicated[:3])
        raise RuntimeError(
            f"ID hash collision detected in {context}: {len(duplicated)} duplicated hash value(s); examples: {examples}"
        )
    return df


def hash_identifier(identifier, salt=None):
    selected_salt = get_hash_salt() if salt is None else str(salt)
    payload = f"{selected_salt}:{_canonical_identifier(identifier)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]
