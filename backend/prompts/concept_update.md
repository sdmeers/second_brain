<!-- Merge a new source into an existing concept/atlas page (processing/concept_updater.py). Vars: $page_kind, $concept, $existing_content, $new_summary, $source_title, $atlas_guard -->
Here is the existing ${page_kind} page for '${concept}':
${existing_content}

Here is a new source summary that references it:
${new_summary}

Please update the page to integrate any new information, note contradictions, and add a backlink to the new source ([[${source_title}]]). Return the complete updated Markdown.${atlas_guard}
