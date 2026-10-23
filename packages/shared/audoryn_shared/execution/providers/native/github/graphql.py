"""Named operations only. Every caller verifies node ownership before mutation."""

QUERIES = {
    "project_item": (
        "query($id:ID!){node(id:$id){... on ProjectV2Item{id isArchived project{id} content{... on Issue{id repository{databaseId}} ... on PullRequest{id repository{databaseId}}} fieldValues(first:50){nodes{... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{id}}} ... on ProjectV2ItemFieldNumberValue{number field{... on ProjectV2FieldCommon{id}}} ... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{id}}} ... on ProjectV2ItemFieldIterationValue{iterationId field{... on ProjectV2FieldCommon{id}}} ... on ProjectV2ItemFieldSingleSelectValue{optionId field{... on ProjectV2FieldCommon{id}}}}}}}}",
        ("id",),
    ),
    "discussions_search": (
        "query($query:String!,$after:String){search(type:DISCUSSION,query:$query,first:50,after:$after){nodes{... on Discussion{id title body url repository{databaseId}}} pageInfo{hasNextPage endCursor}}}",
        ("query", "after"),
    ),
    "ready": (
        "mutation($nodeId:ID!){markPullRequestReadyForReview(input:{pullRequestId:$nodeId}){pullRequest{id url isDraft}}}",
        ("nodeId",),
    ),
    "resolve": (
        "mutation($threadId:ID!){resolveReviewThread(input:{threadId:$threadId}){thread{id isResolved}}}",
        ("threadId",),
    ),
    "project_read": (
        "query($projectId:ID!,$after:String){node(id:$projectId){... on ProjectV2{id title url fields(first:100){nodes{... on ProjectV2Field{id name dataType} ... on ProjectV2SingleSelectField{id name options{id name}} ... on ProjectV2IterationField{id name configuration{iterations{id title startDate duration}}}}} items(first:50,after:$after){nodes{id isArchived content{... on Issue{id title url repository{databaseId}} ... on PullRequest{id title url repository{databaseId}} ... on DraftIssue{id title}} fieldValues(first:50){nodes{... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{id name}}} ... on ProjectV2ItemFieldNumberValue{number field{... on ProjectV2FieldCommon{id name}}} ... on ProjectV2ItemFieldIterationValue{iterationId field{... on ProjectV2FieldCommon{id name}}} ... on ProjectV2ItemFieldTextValue{text field{... on ProjectV2FieldCommon{id name}}} ... on ProjectV2ItemFieldSingleSelectValue{name optionId field{... on ProjectV2FieldCommon{id name}}}}}} pageInfo{hasNextPage endCursor}}}}}",
        ("projectId", "after"),
    ),
    "project_add": (
        "mutation($projectId:ID!,$contentId:ID!){addProjectV2ItemById(input:{projectId:$projectId,contentId:$contentId}){item{id}}}",
        ("projectId", "contentId"),
    ),
    "project_update": (
        "mutation($projectId:ID!,$itemId:ID!,$fieldId:ID!,$value:ProjectV2FieldValue!){updateProjectV2ItemFieldValue(input:{projectId:$projectId,itemId:$itemId,fieldId:$fieldId,value:$value}){projectV2Item{id}}}",
        ("projectId", "itemId", "fieldId", "value"),
    ),
    "project_archive": (
        "mutation($projectId:ID!,$itemId:ID!){archiveProjectV2Item(input:{projectId:$projectId,itemId:$itemId}){item{id isArchived}}}",
        ("projectId", "itemId"),
    ),
    "discussions_read": (
        "query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){discussions(first:50,after:$after){nodes{id number title body url updatedAt} pageInfo{hasNextPage endCursor}}}}",
        ("owner", "name", "after"),
    ),
    "discussion_read": (
        "query($owner:String!,$name:String!,$number:Int!,$after:String){repository(owner:$owner,name:$name){discussion(number:$number){id title body url author{login} comments(first:50,after:$after){nodes{id body url author{login}} pageInfo{hasNextPage endCursor}}}}}",
        ("owner", "name", "number", "after"),
    ),
    "discussion_categories": (
        "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){discussionCategories(first:100){nodes{id name description}}}}",
        ("owner", "name"),
    ),
    "discussion_create": (
        "mutation($repositoryId:ID!,$categoryId:ID!,$title:String!,$body:String!){createDiscussion(input:{repositoryId:$repositoryId,categoryId:$categoryId,title:$title,body:$body}){discussion{id url title body}}}",
        ("repositoryId", "categoryId", "title", "body"),
    ),
    "discussion_comment": (
        "mutation($discussionId:ID!,$body:String!){addDiscussionComment(input:{discussionId:$discussionId,body:$body}){comment{id url body}}}",
        ("discussionId", "body"),
    ),
    "discussion_update": (
        "mutation($discussionId:ID!,$title:String!,$body:String!){updateDiscussion(input:{discussionId:$discussionId,title:$title,body:$body}){discussion{id title body url}}}",
        ("discussionId", "title", "body"),
    ),
}
