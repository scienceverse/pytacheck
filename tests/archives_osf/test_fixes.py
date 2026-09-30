"""metacheck bugs fixed in the OSF port (docs/UPSTREAM_ISSUES.md U51, U52).

The parity cases of these fixes are marked ``known_divergence``; these tests
pin the fixed behaviour.
"""

from __future__ import annotations

import warnings

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives.osf import osf_check_id
from metacheck.archives.osf_helpers import _osf_file_data, _osf_info, _osf_parse_response


def test_osf_check_id_route_names_are_not_ids() -> None:
    # U51: metacheck takes the last 5-letter segment ("files", "forks")
    assert osf_check_id("https://osf.io/j3gcx/files/") == "j3gcx"
    assert osf_check_id("https://osf.io/j3gcx/forks") == "j3gcx"
    assert osf_check_id("https://osf.io/j3gcx/files/osfstorage") == "j3gcx"
    assert osf_check_id("https://osf.io/preprints/psyarxiv/abcde/") == "abcde"
    # a 24-character segment is a file id only when it is hexadecimal (an OSF
    # object id) and not the registration schema of a draft-registration page
    assert (
        osf_check_id("https://osf.io/abcde/5f0c1ab2c3d4e5f60718293a") == "5f0c1ab2c3d4e5f60718293a"
    )
    assert osf_check_id("https://osf.io/abcde/a-24-character-segment-") == "abcde"
    assert osf_check_id("https://osf.io/abcde/averyveryverylongsegment") == "abcde"
    # (problems/0956797617737129.xml cites a Study 2b preregistration this way;
    # metacheck returns the schema id)
    assert osf_check_id("http://www.osf.io/a5bmw/register/565fb3678c5e4a66b5582f67") == "a5bmw"
    # a URL naming two ids gives the first (metacheck: not an ID)
    assert osf_check_id("osf.io/abcde/ osf.io/fghij/") == "abcde"


def test_osf_check_id_does_not_cut_words_to_ids() -> None:
    # U51: metacheck takes the first five characters after osf.io/ of any word
    # ("prere" for the OSF Preregistration page, "regis" for OSF Registries)
    for url in [
        "https://osf.io/prereg/",
        "osf.io/registries",
        "https://osf.io/registries/osf/discover",
        "https://osf.io/preprints/psyarxiv/abcde/download",
        "https://osf.io/averyveryverylongsegment",
    ]:
        with pytest.warns(UserWarning, match="not a valid OSF ID"):
            assert osf_check_id(url) is None, url
    # an ID followed by punctuation, a slash or a version is still found
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert osf_check_id("osf.io/abcde.") == "abcde"
        assert osf_check_id("osf.io/abcde/files/") == "abcde"
        assert osf_check_id("osf.io/abcde_v2)") == "abcde_v2"
        assert osf_check_id("https://osf.io/abcde/download") == "abcde"


def test_versioned_guids_are_looked_up_as_guids() -> None:
    # U52: metacheck sends abcde_v1 to /files/ (only 5-character ids are GUIDs)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handler)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _osf_info("abcde_v1")
    assert seen and all("/guids/abcde_v1" in u for u in seen)


def test_unknown_type_rows_keep_their_own_id() -> None:
    # U52: metacheck labels them with the osf_id argument (NA for a listing)
    recs = [{"id": "w1", "type": "wikis"}, {"id": "w2", "type": "wikis"}]
    with pytest.warns(UserWarning, match="w1 has unknown type: wikis"):
        out = _osf_parse_response(recs)
    assert out["osf_id"].tolist() == ["w1", "w2"]
    assert out["osf_type"].tolist() == ["unknown", "unknown"]


def test_file_data_names_only_folders_after_their_provider() -> None:
    # U52: on several records at once, metacheck names every unnamed row
    # after its provider once any folder is present
    recs = [
        {"id": "d1", "type": "files", "attributes": {"kind": "folder", "provider": "osfstorage"}},
        {"id": "f1", "type": "files", "attributes": {"kind": "file", "provider": "osfstorage"}},
    ]
    out = _osf_file_data(recs)
    assert out["name"].tolist()[0] == "osfstorage"
    assert pd.isna(out["name"].iloc[1])
