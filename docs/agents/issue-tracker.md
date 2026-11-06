# Issue tracker: Asana

Issues, specs and tickets for this repo live as tasks in Joe's Asana, in a project named **Tiny Engineer**. Use the Asana connector tools (`search_objects`, `get_task`, `get_tasks`, `create_tasks`, `update_tasks`, `add_comment`), never the GitHub issue tracker.

The project doesn't exist yet. The first time a skill needs to publish, ask Joe once, then create it (one project, sections optional).

## Conventions

- **Create an issue**: a task in the Tiny Engineer project. Title = the issue title; description = the body.
- **Read an issue**: `get_task` plus `get_task_stories` for the comments.
- **List issues**: `get_tasks` for the project, or `search_tasks` filtered by tag.
- **Comment**: `add_comment` on the task.
- **Labels**: Asana tags, using the strings in `docs/agents/triage-labels.md`. Create a tag the first time it's needed.
- **Close**: mark the task complete, with a comment saying why.
- **Specs and tickets**: a spec is one task; its tickets are subtasks of it. "Blocked by" becomes an Asana dependency.

## When a skill says "publish to the issue tracker"

Create a task in the Tiny Engineer project (ask Joe first, per his route skill).

## When a skill says "fetch the relevant ticket"

Find it with `search_objects` (resource_type task) and read it with `get_task`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is one task tagged `wayfinder:map`; its tickets are subtasks.
