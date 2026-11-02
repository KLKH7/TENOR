// Shapes of the Granola public API (GET /v1/notes/{id}?include=transcript), as written to
// data/fixtures by FixtureSource-compatible generators. See docs/SCHEMA_NOTES.md.

export interface GranolaUser {
  name: string | null;
  email: string;
}

export interface Speaker {
  source: "microphone" | "speaker";
  attribution?: "me" | "them";
  diarization_label?: string;
  name?: string;
}

export interface TranscriptItem {
  speaker: Speaker;
  text: string;
  start_time: string;
  end_time: string;
}

export interface Folder {
  id: string;
  name: string;
  parent_folder_id: string | null;
}

export interface Note {
  id: string;
  title: string | null;
  owner: GranolaUser;
  created_at: string;
  updated_at: string;
  web_url: string;
  calendar_event: {
    event_title: string | null;
    scheduled_start_time: string | null;
    scheduled_end_time: string | null;
  } | null;
  attendees: GranolaUser[];
  folder_membership: Folder[];
  summary_markdown: string | null;
  private_notes_markdown: string | null;
  transcript: TranscriptItem[] | null;
}

export interface DirectoryPerson {
  id: string;
  name: string;
  email: string;
  role: string;
  team: string;
  org: string;
}

export interface Directory {
  people: DirectoryPerson[];
  orgs: { id: string; name: string; kind: string; domain: string }[];
  projects: { id: string; name: string; aliases: string[]; description: string }[];
}
