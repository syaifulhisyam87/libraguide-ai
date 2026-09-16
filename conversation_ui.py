import streamlit as st

import conversation_memory as cm


def initialise_conversation_session():
    """Create the active-conversation session variable."""
    if "active_conversation_id" not in st.session_state:
        st.session_state.active_conversation_id = None


def start_new_conversation():
    """Clear the screen without deleting saved conversations."""
    st.session_state.messages = []
    st.session_state.active_conversation_id = None


def render_history_panel(profile):
    """Display conversation-history controls."""
    initialise_conversation_session()
    cm.init_database()

    if not profile:
        st.session_state.active_conversation_id = None
        return

    user_id = profile["id"]

    with st.expander("💬 Conversation history"):
        stored_setting = cm.get_history_enabled(user_id)

        selected_setting = st.toggle(
            "Save my conversation history",
            value=stored_setting,
            key=f"history_setting_{user_id}",
            help=(
                "When enabled, your questions and LibraGuide's "
                "answers are stored locally under your account."
            ),
        )

        if selected_setting != stored_setting:
            cm.set_history_enabled(
                user_id,
                selected_setting,
            )

            if not selected_setting:
                st.session_state.active_conversation_id = None

            st.rerun()

        if not selected_setting:
            st.caption(
                "Conversation saving is disabled. Current chats "
                "will disappear when the session ends."
            )
            return

        st.success("Conversation saving is enabled.")

        if st.button(
            "Start a new conversation",
            use_container_width=True,
            key=f"new_conversation_{user_id}",
        ):
            start_new_conversation()
            st.rerun()

        conversations = cm.list_conversations(user_id)

        if not conversations:
            st.caption(
                "No conversations have been saved yet. "
                "Your next successful chat will appear here."
            )
            return

        conversation_labels = {
            (
                f"{conversation['title']} "
                f"— {conversation['updated_at']}"
            ): conversation["id"]
            for conversation in conversations
        }

        labels = list(conversation_labels.keys())
        active_id = st.session_state.active_conversation_id
        selected_index = 0

        if active_id:
            for index, label in enumerate(labels):
                if conversation_labels[label] == active_id:
                    selected_index = index
                    break

        selected_label = st.selectbox(
            "Saved conversations",
            options=labels,
            index=selected_index,
            key=f"saved_conversation_{user_id}",
        )

        selected_id = conversation_labels[selected_label]

        load_column, delete_column = st.columns(2)

        with load_column:
            if st.button(
                "Load",
                use_container_width=True,
                key=f"load_conversation_{user_id}",
            ):
                saved_chat = cm.load_conversation(
                    user_id,
                    selected_id,
                )

                if saved_chat:
                    st.session_state.messages = (
                        saved_chat["messages"]
                    )

                    st.session_state.active_conversation_id = (
                        selected_id
                    )

                    saved_mode = saved_chat[
                        "conversation"
                    ]["answer_mode"]

                    st.session_state.answer_mode = saved_mode
                    st.session_state.previous_answer_mode = (
                        saved_mode
                    )

                    st.rerun()
                else:
                    st.error(
                        "The conversation could not be loaded."
                    )

        with delete_column:
            if st.button(
                "Delete",
                use_container_width=True,
                key=f"delete_conversation_{user_id}",
            ):
                deleted = cm.delete_conversation(
                    user_id,
                    selected_id,
                )

                if deleted:
                    if (
                        st.session_state.active_conversation_id
                        == selected_id
                    ):
                        start_new_conversation()

                    st.rerun()
                else:
                    st.error(
                        "The conversation could not be deleted."
                    )

        st.caption(
            f"{len(conversations)} saved conversation(s)"
        )

        st.divider()

        confirm_delete_all = st.checkbox(
            "Confirm deletion of all my saved conversations",
            key=f"confirm_delete_all_{user_id}",
        )

        if st.button(
            "Delete all conversation history",
            use_container_width=True,
            disabled=not confirm_delete_all,
            key=f"delete_all_conversations_{user_id}",
        ):
            cm.delete_all_conversations(user_id)
            start_new_conversation()
            st.rerun()