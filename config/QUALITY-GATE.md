# Quality Gate Evidence

The quality gate intentionally fails closed. It requires:
1. `TEST_COMMAND`: a project-specific test command.
2. `INDEPENDENT_REVIEW_FILE`: a review artifact produced by a separate reviewer/process.

Do not use the same model output as both implementation and independent review evidence.
