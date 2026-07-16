"""ACSOS 2026 conference assistant service.

The package is organised into small modules with a single responsibility each:

* :mod:`llm_service.config`     -- environment parsing helpers and shared paths.
* :mod:`llm_service.text`       -- language-aware tokenisation and normalisation.
* :mod:`llm_service.schemas`    -- request/response and internal data models.
* :mod:`llm_service.formatting` -- presentation helpers over conference data.
* :mod:`llm_service.knowledge`  -- deterministic retrieval over the local data.
* :mod:`llm_service.tools`      -- sandboxed arithmetic and data-analysis tools.
* :mod:`llm_service.models`     -- Ollama/Gemini chat-model factories.
* :mod:`llm_service.agents`     -- grounded responders and their fallback chain.
* :mod:`llm_service.pipeline`   -- the :class:`AnswerService` answer pipeline.
* :mod:`llm_service.app`        -- the thin FastAPI HTTP entrypoint.
* :mod:`llm_service.conference_live` -- bounded live website retrieval.
"""
