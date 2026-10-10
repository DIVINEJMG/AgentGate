from __future__ import annotations

import json
from urllib.parse import quote

from app.execution.contracts import ResourceDescriptor
from app.execution.providers.integration_hooks import ResourcePage


def allowed_actions(actions, scopes):
    grants = dict(scope.split(":", 1) for scope in scopes if ":" in scope)
    return tuple(
        a.scope
        for a in actions
        if grants.get(a.permission.split(":")[0]) == "write"
        or grants.get(a.permission.split(":")[0]) == a.permission.split(":")[1]
        or a.permission == "metadata:read"
    )


class GitHubResources:
    def __init__(self, provider):
        self.provider = provider

    def repository(self, data, scopes=()):
        from .catalog import assembled_catalog

        actions = assembled_catalog()
        available = {c.scope for c in self.provider.manifest.capabilities}
        # An opaque static token cannot establish provider consent from its text.
        # Listing a capability never grants it: exact task grants and GitHub's
        # own permission checks still apply at execution.
        caps = (
            allowed_actions(actions, scopes)
            if scopes
            else tuple(a.scope for a in actions if a.resource_type == "repository")
        )
        full_name = data["full_name"]
        return ResourceDescriptor(
            id="github:repository:" + str(data["id"]),
            provider="github",
            resource_type="repository",
            external_id=str(data["id"]),
            display_name=full_name,
            metadata={
                "aliases": [full_name.lower()],
                "legacyConfigurationKeys": ["repository"],
                "defaultBranch": data.get("default_branch"),
                "archived": data.get("archived", False),
            },
            health="healthy",
            available_capabilities=tuple(
                c for c in caps if c.startswith("github.repository.") and c in available
            ),
            web_url=data["html_url"],
            configuration={
                "repository": full_name,
                "repositoryId": str(data["id"]),
                "repositoryNodeId": data.get("node_id", ""),
                "defaultBranch": data.get("default_branch", "main"),
            },
        )

    async def page(self, *, configuration, credential, cursor):
        installation = configuration.get("installationId")
        scopes = (
            tuple(configuration.get("githubPermissionScopes", "").split(","))
            if configuration.get("githubPermissionScopes")
            else ()
        )
        if not installation:
            repo = configuration.get("repository", "")
            data = (
                await self.provider.api("GET", "/repos/" + quote(repo, safe="/"), credential)
            ).data
            return ResourcePage((self.repository(data, scopes),))
        if cursor and cursor.startswith("projects:"):
            return await self.projects(configuration, credential, cursor.split(":", 1)[1] or None)
        page = int(cursor.split(":")[1]) if cursor else 1
        response = await self.provider.api(
            "GET", f"/installation/repositories?per_page=100&page={page}", credential
        )
        rows = response.data["repositories"]
        next_cursor = (
            f"repos:{page + 1}"
            if len(rows) == 100
            else "projects:"
            if configuration.get("accountType") == "Organization"
            and any(s.startswith("organization_projects:") for s in scopes)
            else None
        )
        return ResourcePage(tuple(self.repository(r, scopes) for r in rows), next_cursor)

    async def projects(self, configuration, credential, after):
        from .catalog import assembled_catalog

        data = await self.provider.graphql(
            "query($login:String!,$after:String){organization(login:$login){projectsV2(first:50,after:$after){nodes{id number title url} pageInfo{hasNextPage endCursor}}}}",
            {"login": configuration["ownerLogin"], "after": after},
            credential,
        )
        project_data = data["organization"]["projectsV2"]
        scopes = tuple(configuration.get("githubPermissionScopes", "").split(","))
        caps = tuple(
            a.scope
            for a in assembled_catalog()
            if a.resource_type == "project"
            and a.scope in allowed_actions(assembled_catalog(), scopes)
        )
        resources = tuple(
            ResourceDescriptor(
                id="github:project:" + row["id"],
                provider="github",
                resource_type="project",
                external_id=row["id"],
                display_name=row["title"],
                metadata={"number": row["number"]},
                health="healthy",
                available_capabilities=caps,
                web_url=row["url"],
                configuration={
                    "projectNodeId": row["id"],
                    "ownerLogin": configuration["ownerLogin"],
                },
            )
            for row in project_data["nodes"]
            if row["id"] in json.loads(configuration.get("authorizedProjectIds", "[]"))
        )
        info = project_data["pageInfo"]
        return ResourcePage(
            resources, "projects:" + info["endCursor"] if info["hasNextPage"] else None
        )

    async def lookup(self, *, external_id, configuration, credential):
        if configuration.get("projectNodeId"):
            data = await self.provider.graphql(
                "query($id:ID!){node(id:$id){... on ProjectV2{id number title url}}}",
                {"id": external_id},
                credential,
            )
            row = data["node"]
            return ResourceDescriptor(
                id="github:project:" + row["id"],
                provider="github",
                resource_type="project",
                external_id=row["id"],
                display_name=row["title"],
                metadata={"number": row["number"]},
                health="healthy",
                available_capabilities=self.provider.project_capabilities(),
                web_url=row["url"],
                configuration=configuration,
            )
        path = (
            "/repositories/" + external_id
            if external_id.isdigit()
            else "/repos/" + quote(external_id, safe="/")
        )
        response = await self.provider.api("GET", path, credential)
        return self.repository(
            response.data, tuple(configuration.get("githubPermissionScopes", "").split(","))
        )
