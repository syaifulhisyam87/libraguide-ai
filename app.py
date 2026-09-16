import hmac

import requests
import streamlit as st

import account_ui
import conversation_memory as cm
import conversation_ui
import feedback_ui
import knowledge_base as kb
import verified_notes as vn


APP_NAME = "LibraGuide AI"
CHAT_MODEL = "qwen3:4b"
CHAT_URL = "http://localhost:11434/api/chat"
TOP_K = 5

GENERAL_SYSTEM_PROMPT = """
You are LibraGuide AI, a professional academic library research assistant.

You help users:
- Develop research topics and questions.
- Identify keywords, synonyms, and controlled vocabulary.
- Construct Boolean database search strategies.
- Evaluate scholarly and grey literature.
- Understand referencing, research ethics, open access, bibliometrics,
  scholarly communication, and predatory publishing.

Accuracy rules:
- Never invent authors, article titles, journals, DOIs, or references.
- Never claim that you searched a live database or website.
- Clearly identify information that requires external verification.
- Respond in the same language as the user unless asked otherwise.
"""

KNOWLEDGE_SYSTEM_PROMPT = """
You are LibraGuide AI, a document-grounded library knowledge assistant.

Use only the retrieved knowledge-base passages provided with the question.

Rules:
1. Do not use outside knowledge to fill gaps.
2. Use the exact citation label supplied with each passage.
   Document citations use [filename.pdf, p. 4]. Verified-note
   citations use [Verified Library Note #12].
3. Never invent citations, quotations, authors, findings, or page numbers.
4. If the evidence is insufficient, clearly say:
   "The knowledge base does not provide enough information to answer this."
5. Distinguish document statements from your interpretation.
6. Keep direct quotations short and place them inside quotation marks.
7. Respond in the same language as the user's question.
"""


def passage_citation(passage):
    """Return the supplied citation label for a passage."""
    if passage.get("citation"):
        return passage["citation"]

    return (
        f"{passage['source']}, "
        f"p. {passage['page']}"
    )


def build_context(passages):
    """Format retrieved passages for Qwen."""
    sections = []

    for passage in passages:
        citation = passage_citation(passage)
        details = []

        if passage.get("title"):
            details.append(
                f"Title: {passage['title']}"
            )

        if passage.get("source_reference"):
            details.append(
                "Verification source: "
                f"{passage['source_reference']}"
            )

        details.append(passage["text"])

        sections.append(
            f"[Source: {citation}]\n"
            + "\n".join(details)
        )

    return "\n\n".join(sections)


def show_evidence(evidence):
    """Show users exactly which passages were retrieved."""
    if not evidence:
        return

    unique_sources = []

    for item in evidence:
        label = passage_citation(item)

        if label not in unique_sources:
            unique_sources.append(label)

    st.caption(
        "Retrieved sources: "
        + " • ".join(unique_sources)
    )

    with st.expander("View retrieved evidence"):
        for number, item in enumerate(
            evidence,
            start=1,
        ):
            citation = passage_citation(item)

            st.markdown(
                f"**{number}. {citation}**"
            )

            if item.get("title"):
                st.write(item["title"])

            st.write(item["text"])

            if item.get("source_reference"):
                st.caption(
                    "Verification source: "
                    f"{item['source_reference']}"
                )

            st.caption(
                "Semantic similarity: "
                f"{item['score']:.3f}"
            )


st.set_page_config(
    page_title=APP_NAME,
    page_icon="📚",
    layout="wide",
)

kb.init_database()
vn.init_database()

if "messages" not in st.session_state:
    st.session_state.messages = []

if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False

documents = kb.list_documents()
verified_note_count = vn.active_note_count()

st.title("📚 LibraGuide AI")
st.subheader("Persistent Library Knowledge Assistant")
st.caption(
    "Powered locally by Ollama, Qwen3, "
    "EmbeddingGemma, SQLite and Streamlit"
)

with st.sidebar:
    st.header("LibraGuide")

    current_profile = account_ui.render_account_panel()

    st.caption(
        "Current user: "
        f"{account_ui.current_user_label()}"
    )

    conversation_ui.render_history_panel(
        current_profile
    )

    st.divider()

    with st.expander("🔐 Administrator panel"):
        if not st.session_state.admin_authenticated:
            st.write(
                "Administrator access is required to "
                "modify the shared knowledge base."
            )

            entered_password = st.text_input(
                "Administrator password",
                type="password",
                key="admin_password_input",
            )

            if st.button(
                "Administrator sign in",
                type="primary",
                use_container_width=True,
            ):
                stored_password = st.secrets.get(
                    "ADMIN_PASSWORD",
                    "",
                )

                if (
                    stored_password
                    and hmac.compare_digest(
                        entered_password,
                        stored_password,
                    )
                ):
                    st.session_state.admin_authenticated = True
                    st.rerun()
                else:
                    st.error(
                        "Incorrect administrator password."
                    )

        else:
            st.success(
                "Administrator access is active."
            )

            admin_files = st.file_uploader(
                "Add or update knowledge documents",
                type=["pdf"],
                accept_multiple_files=True,
                help=(
                    "A new PDF with an existing filename "
                    "replaces its previous version."
                ),
            )

            if st.button(
                "Add documents to knowledge base",
                type="primary",
                use_container_width=True,
                disabled=not admin_files,
            ):
                for uploaded_file in admin_files:
                    try:
                        with st.spinner(
                            f"Indexing {uploaded_file.name}..."
                        ):
                            result = kb.index_pdf(
                                uploaded_file.name,
                                uploaded_file.getvalue(),
                            )

                        if result["status"] == "indexed":
                            st.success(result["message"])
                        else:
                            st.info(result["message"])

                    except requests.exceptions.ConnectionError:
                        st.error(
                            f"{uploaded_file.name}: cannot "
                            "connect to Ollama."
                        )

                    except requests.exceptions.Timeout:
                        st.error(
                            f"{uploaded_file.name}: indexing "
                            "timed out."
                        )

                    except Exception as error:
                        st.error(
                            f"{uploaded_file.name}: {error}"
                        )

                documents = kb.list_documents()

            st.divider()
            st.markdown("**Remove a document**")

            if documents:
                document_labels = {
                    (
                        f"{document['filename']} "
                        f"({document['page_count']} pages)"
                    ): document["id"]
                    for document in documents
                }

                selected_label = st.selectbox(
                    "Select document",
                    options=list(document_labels.keys()),
                )

                confirm_deletion = st.checkbox(
                    "I understand this permanently "
                    "removes the document."
                )

                if st.button(
                    "Delete selected document",
                    use_container_width=True,
                    disabled=not confirm_deletion,
                ):
                    document_id = document_labels[
                        selected_label
                    ]

                    if kb.delete_document(document_id):
                        st.success(
                            "The document was removed."
                        )
                        documents = kb.list_documents()
                    else:
                        st.error(
                            "The document could not be found."
                        )
            else:
                st.caption(
                    "There are no documents to remove."
                )

            st.divider()
            feedback_ui.render_admin_feedback_panel()

            if st.button(
                "Administrator sign out",
                use_container_width=True,
            ):
                st.session_state.admin_authenticated = False
                st.rerun()

    st.divider()
    st.subheader("Knowledge-base status")

    documents = kb.list_documents()

    if documents or verified_note_count:
        total_chunks = sum(
            document["chunk_count"]
            for document in documents
        )

        st.success(
            f"{len(documents)} document(s) and "
            f"{verified_note_count} verified note(s) available"
        )

        if documents:
            st.caption(
                f"{total_chunks} searchable document passages"
            )

        if verified_note_count:
            st.caption(
                f"{verified_note_count} librarian-verified "
                "knowledge note(s)"
            )

        for document in documents:
            st.write(
                f"• {document['filename']} "
                f"({document['page_count']} pages)"
            )
    else:
        st.warning(
            "The shared knowledge base is empty."
        )

    st.divider()

    available_modes = [
        "Knowledge base",
        "General library assistant",
    ]

    if documents or verified_note_count:
        if (
            "answer_mode" not in st.session_state
            or st.session_state.answer_mode
            not in available_modes
        ):
            st.session_state.answer_mode = (
                "Knowledge base"
            )

        answer_mode = st.radio(
            "Answer mode",
            options=available_modes,
            key="answer_mode",
            help=(
                "Knowledge-base mode uses only approved "
                "documents. General mode uses the model's "
                "existing knowledge."
            ),
        )
    else:
        st.session_state.answer_mode = (
            "General library assistant"
        )
        answer_mode = "General library assistant"

    if st.button(
        "Clear conversation",
        use_container_width=True,
    ):
        st.session_state.messages = []
        st.session_state.active_conversation_id = None
        st.rerun()

    feedback_ui.render_user_feedback_panel(
        current_profile,
        answer_mode,
    )

profile_context = account_ui.build_profile_context(
    current_profile
)

if "previous_answer_mode" not in st.session_state:
    st.session_state.previous_answer_mode = answer_mode
if st.session_state.previous_answer_mode != answer_mode:
    st.session_state.messages = []
    st.session_state.active_conversation_id = None
    st.session_state.previous_answer_mode = answer_mode

if answer_mode == "Knowledge base":
    st.success(
        "Knowledge-base mode is active. Responses will "
        "use approved documents and verified knowledge notes."
    )
else:
    st.info(
        "General assistant mode is active. Responses are "
        "not grounded in the persistent documents."
    )

if not st.session_state.messages:
    with st.chat_message("assistant"):
        if answer_mode == "Knowledge base":
            st.markdown(
                """
                Hello! I am **LibraGuide AI**.

                Ask me a question about the approved library
                knowledge base. I will retrieve relevant document
                passages and librarian-verified notes with citations.
                """
            )
        else:
            st.markdown(
                """
                Hello! I am **LibraGuide AI**.

                I can help with research topics, keywords,
                database search strategies, information literacy,
                referencing and scholarly communication.
                """
            )

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

        if message["role"] == "assistant":
            show_evidence(
                message.get("evidence", [])
            )

prompt = st.chat_input(
    "Ask a library or research question..."
)

if prompt:
    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
        }
    )

    with st.chat_message("user"):
        st.markdown(prompt)

    evidence = []

    try:
        if answer_mode == "Knowledge base":
            with st.spinner(
                "Searching the knowledge base..."
            ):
                    document_evidence = kb.search_knowledge_base(
                        prompt,
                        top_k=TOP_K,
                    )

                    note_evidence = (
                        vn.search_verified_notes(
                            prompt,
                            top_k=TOP_K,
                        )
                    )

                    evidence = sorted(
                        document_evidence + note_evidence,
                        key=lambda item: item["score"],
                        reverse=True,
                    )[:TOP_K]

            if not evidence:
                answer = (
                    "The knowledge base does not contain "
                    "any searchable information yet."
                )

                with st.chat_message("assistant"):
                    st.markdown(answer)

            else:
                document_context = build_context(
                    evidence
                )

                augmented_question = f"""
User question:
{prompt}

Retrieved knowledge-base passages:
{document_context}

Answer only from these passages. Use each passage's exact
citation label for supported claims.
"""

                recent_history = (
                    st.session_state.messages[:-1][-6:]
                )

                conversation = (
                    [
                        {
                            "role": "system",
                            "content": (
                                KNOWLEDGE_SYSTEM_PROMPT
                                + profile_context
                            ),
                        }
                    ]
                    + recent_history
                    + [
                        {
                            "role": "user",
                            "content": augmented_question,
                        }
                    ]
                )

                with st.chat_message("assistant"):
                    with st.spinner(
                        "Preparing a grounded answer..."
                    ):
                        response = requests.post(
                            CHAT_URL,
                            json={
                                "model": CHAT_MODEL,
                                "messages": conversation,
                                "stream": False,
                                "think": False,
                                "options": {
                                    "temperature": 0.1,
                                    "num_ctx": 4096,
                                },
                            },
                            timeout=300,
                        )

                        response.raise_for_status()
                        answer = response.json()[
                            "message"
                        ]["content"]

                        st.markdown(answer)
                        show_evidence(evidence)

        else:
            conversation = (
                [
                    {
                        "role": "system",
                        "content": (
                            GENERAL_SYSTEM_PROMPT
                            + profile_context
                        ),
                    }
                ]
                + st.session_state.messages[-10:]
            )

            with st.chat_message("assistant"):
                with st.spinner(
                    "LibraGuide is preparing the answer..."
                ):
                    response = requests.post(
                        CHAT_URL,
                        json={
                            "model": CHAT_MODEL,
                            "messages": conversation,
                            "stream": False,
                            "think": False,
                            "options": {
                                "temperature": 0.3,
                                "num_ctx": 4096,
                            },
                        },
                        timeout=300,
                    )

                    response.raise_for_status()
                    answer = response.json()[
                        "message"
                    ]["content"]

                    st.markdown(answer)

        assistant_message = {
            "role": "assistant",
            "content": answer,
            "evidence": evidence,
        }

        st.session_state.messages.append(
            assistant_message
        )

        current_user = st.session_state.get(
            "current_user"
        )

        if (
            current_user
            and cm.get_history_enabled(
                current_user["id"]
            )
        ):
            conversation_id = (
                st.session_state.get(
                    "active_conversation_id"
                )
            )

            if conversation_id is None:
                conversation_id = (
                    cm.create_conversation(
                        user_id=current_user["id"],
                        first_message=prompt,
                        answer_mode=answer_mode,
                    )
                )

                st.session_state[
                    "active_conversation_id"
                ] = conversation_id

            if conversation_id:
                cm.add_message(
                    user_id=current_user["id"],
                    conversation_id=conversation_id,
                    role="user",
                    content=prompt,
                )

                cm.add_message(
                    user_id=current_user["id"],
                    conversation_id=conversation_id,
                    role="assistant",
                    content=answer,
                    evidence=evidence,
                )

        st.rerun()

    except requests.exceptions.ConnectionError:
        st.error(
            "Cannot connect to Ollama. Make sure the "
            "Ollama application is running."
        )

    except requests.exceptions.Timeout:
        st.error(
            "The operation took too long. Try a shorter "
            "question or a smaller document."
        )

    except Exception as error:
        st.error(f"An error occurred: {error}")
