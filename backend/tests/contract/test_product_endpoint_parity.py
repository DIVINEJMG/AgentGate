from app.bootstrap.application import create_application


app = create_application()


def test_product_endpoint_domains_are_mounted_in_v1_and_v2() -> None:
    paths = set(app.openapi()["paths"])
    suffixes = {
        "/organizations/{organization_id}/workforce",
        "/organizations/{organization_id}/jobs",
        "/organizations/{organization_id}/work-items",
        "/organizations/{organization_id}/integrations",
        "/organizations/{organization_id}/capabilities",
        "/organizations/{organization_id}/policies",
        "/organizations/{organization_id}/risk",
        "/organizations/{organization_id}/actions",
        "/organizations/{organization_id}/approvals",
        "/organizations/{organization_id}/incidents",
        "/organizations/{organization_id}/audit",
        "/organizations/{organization_id}/triggers",
        "/organizations/{organization_id}/runtime",
        "/organizations/{organization_id}/memory",
        "/organizations/{organization_id}/artifacts",
        "/organizations/{organization_id}/results",
        "/organizations/{organization_id}/product",
        "/organizations/{organization_id}/commercial",
        "/organizations/{organization_id}/supervision",
        "/organizations/{organization_id}/performance",
    }
    for version in ("v1", "v2"):
        missing = {
            f"/api/{version}{suffix}"
            for suffix in suffixes
            if f"/api/{version}{suffix}" not in paths
        }
        assert not missing, f"Missing {version} product paths: {sorted(missing)}"


def test_mutating_product_contracts_are_mounted() -> None:
    schema = app.openapi()["paths"]
    expected = {
        ("post", "/api/v2/organizations/{organization_id}/jobs"),
        ("post", "/api/v2/organizations/{organization_id}/jobs/{job_id}/run-now"),
        ("post", "/api/v2/organizations/{organization_id}/integrations/connect/{provider}"),
        ("put", "/api/v2/organizations/{organization_id}/agents/{agent_id}/capabilities"),
        ("post", "/api/v2/organizations/{organization_id}/policy-evaluations"),
        ("post", "/api/v2/organizations/{organization_id}/action-gateway/tests"),
        ("post", "/api/v2/organizations/{organization_id}/incidents"),
        ("post", "/api/v2/organizations/{organization_id}/results/backfill"),
        ("put", "/api/v2/organizations/{organization_id}/settings"),
    }
    missing = [
        (method, path)
        for method, path in expected
        if path not in schema or method not in schema[path]
    ]
    assert not missing, f"Missing product mutations: {missing}"
