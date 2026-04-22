create table if not exists brain (
  section    text primary key,
  data       jsonb not null default '{}',
  updated_at timestamptz default now()
);

alter table brain disable row level security;

insert into brain (section, data) values
  ('profile',        '{}'),
  ('settings',       '{}'),
  ('memory',         '[]'),
  ('followups',      '{}'),
  ('context_config', '{}')
on conflict (section) do nothing;