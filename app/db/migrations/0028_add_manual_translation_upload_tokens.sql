-- Short-lived, single-use, item-scoped tokens for
-- POST /api/queue/{item_id}/manual-translation (see AUTH_PLAN.md, a
-- local untracked design doc). This route previously had NO auth at
-- all -- the "presigned URL" pattern here exists specifically because
-- the token minted for one submission may end up echoed into an AI
-- assistant's tool-call response/transcript (a semi-trusted channel),
-- so it must be worthless outside that one submission rather than a
-- standing credential like the MCP/external-translate bearer tokens.
CREATE TABLE manual_translation_upload_tokens (
    token      TEXT PRIMARY KEY,
    item_id    INTEGER NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at    TIMESTAMP,
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX idx_manual_translation_upload_tokens_item ON manual_translation_upload_tokens(item_id);
