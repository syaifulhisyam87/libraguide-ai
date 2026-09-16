import numpy as np
import streamlit as st
from google import genai
from google.genai import types


CHAT_MODEL = "gemini-3.1-flash-lite"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768


@st.cache_resource
def get_client():
    """Create one reusable Gemini client for the Streamlit process."""
    api_key = st.secrets.get("GEMINI_API_KEY", "")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit secrets."
        )

    return genai.Client(api_key=api_key)


def _conversation_parts(messages):
    """Convert OpenAI-style chat messages to Gemini content objects."""
    system_parts = []
    turns = []

    for message in messages:
        role = message.get("role", "")
        content = str(message.get("content", "")).strip()

        if not content:
            continue

        if role == "system":
            system_parts.append(content)
            continue

        gemini_role = "model" if role == "assistant" else "user"

        # A trimmed Streamlit history can begin with an assistant turn.
        # Gemini conversations should begin with the user.
        if gemini_role == "model" and not turns:
            continue

        if turns and turns[-1][0] == gemini_role:
            turns[-1][1] += f"\n\n{content}"
        else:
            turns.append([gemini_role, content])

    contents = [
        types.Content(
            role=role,
            parts=[types.Part.from_text(text=text)],
        )
        for role, text in turns
    ]

    return "\n\n".join(system_parts), contents


def generate_chat(messages, temperature=0.3):
    """Generate a text response from Gemini using chat history."""
    system_instruction, contents = _conversation_parts(messages)

    if not contents:
        raise ValueError("The conversation does not contain a user message.")

    response = get_client().models.generate_content(
        model=CHAT_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=temperature,
            max_output_tokens=2048,
        ),
    )

    answer = (response.text or "").strip()

    if not answer:
        raise RuntimeError(
            "Gemini returned no text. The response may have been blocked."
        )

    return answer


def create_embeddings(
    texts,
    task_type,
    batch_size=16,
):
    """Create normalized Gemini embeddings for documents or queries."""
    if not texts:
        return np.empty(
            (0, EMBEDDING_DIMENSIONS),
            dtype=np.float32,
        )

    all_embeddings = []

    for start in range(0, len(texts), batch_size):
        batch = [
            str(text).strip()
            for text in texts[start:start + batch_size]
        ]

        if any(not text for text in batch):
            raise ValueError("Embedding text must not be empty.")

        response = get_client().models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIMENSIONS,
            ),
        )

        if len(response.embeddings) != len(batch):
            raise RuntimeError(
                "Gemini returned an unexpected number of embeddings."
            )

        all_embeddings.extend(
            embedding.values
            for embedding in response.embeddings
        )

    matrix = np.asarray(all_embeddings, dtype=np.float32)

    if (
        matrix.ndim != 2
        or matrix.shape[1] != EMBEDDING_DIMENSIONS
    ):
        raise RuntimeError(
            "Gemini returned embeddings with unexpected dimensions."
        )

    # gemini-embedding-001 needs manual normalization when using
    # fewer than its default 3072 dimensions.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-12, None)

    return matrix / norms
