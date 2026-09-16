import requests
import streamlit as st

import feedback_memory as fm
import verified_notes as vn


STATUS_LABELS = {
    "pending": "Pending review",
    "approved": "Approved",
    "rejected": "Rejected",
}


def latest_exchange():
    """Return the most recent user question and assistant answer."""
    messages = st.session_state.get("messages", [])

    for assistant_index in range(
        len(messages) - 1,
        -1,
        -1,
    ):
        assistant_message = messages[assistant_index]

        if assistant_message.get("role") != "assistant":
            continue

        for user_index in range(
            assistant_index - 1,
            -1,
            -1,
        ):
            user_message = messages[user_index]

            if user_message.get("role") == "user":
                return {
                    "question": user_message.get(
                        "content",
                        "",
                    ),
                    "answer": assistant_message.get(
                        "content",
                        "",
                    ),
                }

    return None


def show_user_feedback_history(user_id):
    """Show the signed-in user's previous submissions."""
    submissions = fm.list_user_feedback(user_id)

    if not submissions:
        return

    st.divider()
    st.markdown("**My submitted feedback**")

    labels = {}

    for item in submissions:
        question_preview = " ".join(
            item["question"].split()
        )

        if len(question_preview) > 45:
            question_preview = (
                question_preview[:44].rstrip() + "…"
            )

        label = (
            f"#{item['id']} · "
            f"{STATUS_LABELS[item['status']]} · "
            f"{question_preview}"
        )

        labels[label] = item

    selected_label = st.selectbox(
        "Submission",
        options=list(labels.keys()),
        key=f"user_feedback_history_{user_id}",
    )

    selected = labels[selected_label]

    st.caption(
        f"Category: {selected['category']} · "
        f"Submitted: {selected['submitted_at']}"
    )

    st.text_area(
        "Your suggested correction",
        value=selected["suggestion"],
        disabled=True,
        key=f"user_feedback_suggestion_{selected['id']}",
    )

    if selected["admin_notes"]:
        st.text_area(
            "Administrator response",
            value=selected["admin_notes"],
            disabled=True,
            key=f"user_feedback_admin_{selected['id']}",
        )

    if selected["status"] == "pending":
        confirm_withdrawal = st.checkbox(
            "Confirm withdrawal of this pending submission",
            key=f"confirm_feedback_withdrawal_{selected['id']}",
        )

        if st.button(
            "Withdraw pending feedback",
            use_container_width=True,
            disabled=not confirm_withdrawal,
            key=f"withdraw_feedback_{selected['id']}",
        ):
            if fm.delete_pending_feedback(
                user_id,
                selected["id"],
            ):
                st.session_state.feedback_notice = (
                    "The pending feedback was withdrawn."
                )
                st.rerun()


def render_user_feedback_panel(profile, answer_mode):
    """Render the user-facing correction form."""
    fm.init_database()

    with st.expander("📝 Feedback and corrections"):
        notice = st.session_state.pop(
            "feedback_notice",
            None,
        )

        if notice:
            st.success(notice)

        if not profile:
            st.caption(
                "Sign in to submit a correction or track "
                "feedback status."
            )
            return

        exchange = latest_exchange()

        if exchange is None:
            st.caption(
                "Ask LibraGuide a question first. You can then "
                "submit feedback about its latest answer."
            )
        else:
            st.write(
                "Report a problem with LibraGuide's latest "
                "answer. Submissions require administrator review."
            )

            with st.form(
                "feedback_submission_form",
                clear_on_submit=True,
            ):
                category = st.selectbox(
                    "Feedback category",
                    options=list(fm.VALID_CATEGORIES),
                )

                st.text_area(
                    "Question being reviewed",
                    value=exchange["question"],
                    disabled=True,
                )

                st.text_area(
                    "LibraGuide's answer",
                    value=exchange["answer"],
                    disabled=True,
                    height=160,
                )

                suggestion = st.text_area(
                    "What should be corrected or improved?",
                    max_chars=6_000,
                    placeholder=(
                        "Explain the problem and suggest a more "
                        "accurate answer."
                    ),
                )

                supporting_source = st.text_input(
                    "Supporting source or reference (optional)",
                    max_chars=2_000,
                    placeholder=(
                        "Example: policy title, URL, DOI or page"
                    ),
                )

                review_acknowledgement = st.checkbox(
                    "I understand that this suggestion is not "
                    "automatically accepted as factual and must "
                    "be reviewed by an administrator."
                )

                submitted = st.form_submit_button(
                    "Submit for review",
                    type="primary",
                    use_container_width=True,
                )

            if submitted:
                if not review_acknowledgement:
                    st.error(
                        "Confirm the administrator-review notice "
                        "before submitting."
                    )
                else:
                    try:
                        feedback_id = fm.submit_feedback(
                            user_id=profile["id"],
                            category=category,
                            question=exchange["question"],
                            assistant_answer=exchange["answer"],
                            suggestion=suggestion,
                            supporting_source=supporting_source,
                            answer_mode=answer_mode,
                        )

                        st.session_state.feedback_notice = (
                            "Feedback submitted for review "
                            f"as item #{feedback_id}."
                        )
                        st.rerun()

                    except ValueError as error:
                        st.error(str(error))

        show_user_feedback_history(profile["id"])


def feedback_item_label(item):
    """Create a compact administrator queue label."""
    submitter = (
        item["display_name"]
        or item["username"]
        or "Deleted account"
    )

    return (
        f"#{item['id']} · {item['category']} · "
        f"{submitter} · {item['submitted_at']}"
    )


def render_verified_note_publisher(feedback):
    """Publish approved feedback as reviewed knowledge."""
    if feedback["status"] != "approved":
        return

    st.divider()
    st.markdown("#### Publish as verified knowledge")
    st.caption(
        "Publishing is a separate decision from approval. "
        "Edit the wording and independently verify the source."
    )

    existing_note = vn.get_note_by_feedback(
        feedback["id"]
    )

    question_preview = " ".join(
        feedback["question"].split()
    )

    if len(question_preview) > 120:
        question_preview = (
            question_preview[:119].rstrip() + "…"
        )

    default_title = (
        existing_note["title"]
        if existing_note
        else question_preview
    )

    default_content = (
        existing_note["content"]
        if existing_note
        else feedback["suggestion"]
    )

    default_source = (
        existing_note["source_reference"]
        if existing_note
        else feedback["supporting_source"]
    )

    if existing_note:
        note_state = (
            "Active"
            if existing_note["active"]
            else "Retired"
        )

        st.info(
            f"Verified Library Note #{existing_note['id']} "
            f"currently exists. Status: {note_state}."
        )

    with st.form(
        f"verified_note_form_{feedback['id']}"
    ):
        note_title = st.text_input(
            "Verified-note title",
            value=default_title,
            max_chars=200,
        )

        note_content = st.text_area(
            "Verified guidance",
            value=default_content,
            max_chars=8_000,
            height=180,
            help=(
                "Write the final librarian-approved wording. "
                "Do not copy an unverified user claim unchanged."
            ),
        )

        source_reference = st.text_area(
            "Verification source",
            value=default_source,
            max_chars=2_000,
            placeholder=(
                "Record the authoritative document, URL, DOI, "
                "policy title, version and relevant page."
            ),
        )

        verification_confirmed = st.checkbox(
            "I independently verified this wording and source."
        )

        publish = st.form_submit_button(
            (
                "Update and republish note"
                if existing_note
                else "Publish verified note"
            ),
            type="primary",
            use_container_width=True,
        )

    if publish:
        if not verification_confirmed:
            st.error(
                "Confirm independent verification before "
                "publishing."
            )
        else:
            try:
                with st.spinner(
                    "Creating the verified-note embedding..."
                ):
                    note_id = vn.publish_note(
                        feedback_id=feedback["id"],
                        title=note_title,
                        content=note_content,
                        source_reference=source_reference,
                    )

                st.session_state.admin_feedback_notice = (
                    f"Verified Library Note #{note_id} was "
                    "published and is available for retrieval."
                )
                st.rerun()

            except requests.exceptions.ConnectionError:
                st.error(
                    "Cannot connect to Ollama. Start Ollama "
                    "before publishing the note."
                )

            except requests.exceptions.Timeout:
                st.error(
                    "Ollama took too long to create the note "
                    "embedding."
                )

            except ValueError as error:
                st.error(str(error))

            except Exception as error:
                st.error(
                    "The verified note could not be published: "
                    f"{error}"
                )

    if existing_note:
        if existing_note["active"]:
            confirm_state_change = st.checkbox(
                "Confirm retirement of this verified note",
                key=(
                    "confirm_note_retirement_"
                    f"{existing_note['id']}"
                ),
            )

            if st.button(
                "Retire verified note",
                use_container_width=True,
                disabled=not confirm_state_change,
                key=f"retire_note_{existing_note['id']}",
            ):
                vn.set_note_active(
                    existing_note["id"],
                    False,
                )
                st.session_state.admin_feedback_notice = (
                    f"Verified Library Note "
                    f"#{existing_note['id']} was retired."
                )
                st.rerun()

        else:
            if st.button(
                "Reactivate verified note",
                use_container_width=True,
                key=f"reactivate_note_{existing_note['id']}",
            ):
                vn.set_note_active(
                    existing_note["id"],
                    True,
                )
                st.session_state.admin_feedback_notice = (
                    f"Verified Library Note "
                    f"#{existing_note['id']} was reactivated."
                )
                st.rerun()


def render_admin_feedback_panel():
    """Render administrator moderation controls."""
    fm.init_database()

    st.markdown("#### Feedback review queue")

    notice = st.session_state.pop(
        "admin_feedback_notice",
        None,
    )

    if notice:
        st.success(notice)

    counts = fm.feedback_counts()

    st.caption(
        f"Pending: {counts['pending']} · "
        f"Approved: {counts['approved']} · "
        f"Rejected: {counts['rejected']}"
    )

    filter_label = st.selectbox(
        "Review status",
        options=[
            "Pending",
            "Approved",
            "Rejected",
            "All",
        ],
        key="admin_feedback_status_filter",
    )

    status_map = {
        "Pending": "pending",
        "Approved": "approved",
        "Rejected": "rejected",
        "All": None,
    }

    queue = fm.list_feedback(
        status=status_map[filter_label]
    )

    if not queue:
        st.caption(
            "There are no feedback submissions in this view."
        )
        return

    labels = {
        feedback_item_label(item): item
        for item in queue
    }

    selected_label = st.selectbox(
        "Select feedback",
        options=list(labels.keys()),
        key="admin_feedback_selection",
    )

    selected = labels[selected_label]
    submitter = (
        selected["display_name"]
        or selected["username"]
        or "Deleted account"
    )

    st.text(f"Submitted by: {submitter}")
    st.caption(
        f"Answer mode: {selected['answer_mode'] or 'Unknown'} · "
        f"Status: {STATUS_LABELS[selected['status']]}"
    )

    st.text_area(
        "Original question",
        value=selected["question"],
        disabled=True,
        key=f"admin_question_{selected['id']}",
    )

    st.text_area(
        "LibraGuide answer",
        value=selected["assistant_answer"],
        disabled=True,
        height=160,
        key=f"admin_answer_{selected['id']}",
    )

    st.text_area(
        "User's suggested correction",
        value=selected["suggestion"],
        disabled=True,
        key=f"admin_suggestion_{selected['id']}",
    )

    if selected["supporting_source"]:
        st.text_area(
            "User-provided supporting source",
            value=selected["supporting_source"],
            disabled=True,
            key=f"admin_source_{selected['id']}",
        )

    with st.form(
        f"admin_feedback_review_{selected['id']}"
    ):
        admin_notes = st.text_area(
            "Administrator review notes",
            value=selected["admin_notes"],
            max_chars=6_000,
            placeholder=(
                "Record verification findings, reasons or "
                "required document updates."
            ),
        )

        approve_column, reject_column = st.columns(2)

        with approve_column:
            approve = st.form_submit_button(
                "Approve",
                type="primary",
                use_container_width=True,
            )

        with reject_column:
            reject = st.form_submit_button(
                "Reject",
                use_container_width=True,
            )

        reopen = False

        if selected["status"] != "pending":
            reopen = st.form_submit_button(
                "Return to pending review",
                use_container_width=True,
            )

    new_status = None

    if approve:
        new_status = "approved"
    elif reject:
        new_status = "rejected"
    elif reopen:
        new_status = "pending"

    if new_status:
        if fm.review_feedback(
            selected["id"],
            new_status,
            admin_notes,
        ):
            existing_note = vn.get_note_by_feedback(
                selected["id"]
            )

            if (
                new_status != "approved"
                and existing_note
                and existing_note["active"]
            ):
                vn.set_note_active(
                    existing_note["id"],
                    False,
                )

            st.session_state.admin_feedback_notice = (
                f"Feedback #{selected['id']} was marked "
                f"{STATUS_LABELS[new_status].lower()}."
            )
            st.rerun()
        else:
            st.error(
                "The feedback item could not be updated."
            )

    render_verified_note_publisher(selected)
