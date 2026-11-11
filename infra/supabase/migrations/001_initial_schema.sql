-- AgentForge: Supabase Initial Database Schema
-- Migration: 001_initial_schema.sql

-- 1. Graphs Table (Stores AI Agent workflows with JSONB definition)
create table if not exists public.graphs (
    id bigint generated always as identity primary key,
    name text not null,
    description text not null default '',
    definition jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Index for fast listing ordered by recent activity
create index if not exists idx_graphs_updated_at on public.graphs (updated_at desc);

-- GIN index for high-performance JSONB queries inside the workflow definition
create index if not exists idx_graphs_definition_gin on public.graphs using gin (definition);

-- 2. Chat Messages Table (Persistent Copilot Memory)
create table if not exists public.chat_messages (
    id bigint generated always as identity primary key,
    session_id text not null,
    role text not null check (role in ('user', 'assistant', 'system')),
    content text not null,
    graph_data jsonb,
    created_at timestamptz not null default now()
);

-- Compound index for fast chronological message retrieval by session
create index if not exists idx_chat_messages_session_created on public.chat_messages (session_id, created_at asc);

-- 3. Workflow Runs Table
create table if not exists public.runs (
    id bigint generated always as identity primary key,
    graph_id bigint not null references public.graphs(id) on delete cascade,
    workflow_id text not null unique,
    status text not null default 'queued',
    input jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_runs_graph_id on public.runs (graph_id);

-- 4. Enable Row Level Security (RLS)
alter table public.graphs enable row level security;
alter table public.chat_messages enable row level security;
alter table public.runs enable row level security;

-- Policies allowing public read/write for local development & authenticated client access
create policy "Allow all operations for public and anon" on public.graphs
    for all using (true) with check (true);

create policy "Allow all operations for chat messages" on public.chat_messages
    for all using (true) with check (true);

create policy "Allow all operations for runs" on public.runs
    for all using (true) with check (true);
