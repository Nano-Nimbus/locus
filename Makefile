.PHONY: install-skills install-skills-dry install test lint

# Sync the Claude skills from skills/claude/ to ~/.claude/skills/.
# skills/claude/ is the only maintained skill set; CLAUDE_SKILLS_DIR overrides
# the destination.
install-skills:
	@bash scripts/install-skills.sh

install-skills-dry:
	@bash scripts/install-skills.sh --dry-run

# Install the Python package in editable mode
install:
	uv pip install -e .

# Run unit tests
test:
	uv run pytest tests/unit/ -q

# Lint
lint:
	uv run ruff check locus/ tests/
