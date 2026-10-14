create type public.app_role as enum ('admin','operator');
create type public.event_kind as enum ('arm','disarm','trigger','maintenance','observation');

create table public.profiles (id uuid primary key, name text not null default '', email text, created_at timestamptz not null default now());
grant select, insert, update on public.profiles to authenticated; grant all on public.profiles to service_role;
alter table public.profiles enable row level security;
create policy "profiles read" on public.profiles for select to authenticated using (true);
create policy "profiles update own" on public.profiles for update to authenticated using (auth.uid() = id);
create policy "profiles insert own" on public.profiles for insert to authenticated with check (auth.uid() = id);

create table public.user_roles (id uuid primary key default gen_random_uuid(), user_id uuid not null, role app_role not null, unique(user_id, role));
grant select on public.user_roles to authenticated; grant all on public.user_roles to service_role;
alter table public.user_roles enable row level security;
create policy "roles read" on public.user_roles for select to authenticated using (true);

create or replace function public.has_role(_user_id uuid, _role app_role) returns boolean language sql stable security definer set search_path = public
as $$ select exists (select 1 from public.user_roles where user_id = _user_id and role = _role) $$;

create policy "admins manage roles" on public.user_roles for all to authenticated using (public.has_role(auth.uid(),'admin')) with check (public.has_role(auth.uid(),'admin'));
grant insert, delete on public.user_roles to authenticated;

create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, name, email) values (new.id, coalesce(new.raw_user_meta_data->>'name', new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)), new.email);
  insert into public.user_roles (user_id, role) values (new.id, 'operator');
  if not exists (select 1 from public.user_roles where role = 'admin') then
    insert into public.user_roles (user_id, role) values (new.id, 'admin');
  end if;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

create table public.clients (id uuid primary key default gen_random_uuid(), name text not null, address text, notes text, created_at timestamptz not null default now());
create table public.client_users (id uuid primary key default gen_random_uuid(), client_id uuid not null references public.clients(id) on delete cascade, name text not null, role text, created_at timestamptz not null default now());
create table public.client_devices (id uuid primary key default gen_random_uuid(), client_id uuid not null references public.clients(id) on delete cascade, name text not null, type text not null default 'Teclado', created_at timestamptz not null default now());
create table public.client_sensors (id uuid primary key default gen_random_uuid(), client_id uuid not null references public.clients(id) on delete cascade, zone text not null, name text not null, type text not null default 'Sensor infravermelho', created_at timestamptz not null default now());

create table public.client_events (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  kind event_kind not null,
  event_date date not null default current_date,
  event_time time,
  user_name text,
  device_name text,
  by_operator boolean not null default false,
  sensor_id uuid references public.client_sensors(id) on delete set null,
  description text,
  status text,
  operator_id uuid,
  archived boolean not null default false,
  created_at timestamptz not null default now()
);
create index on public.client_events (client_id, event_date);

create table public.bypasses (
  id uuid primary key default gen_random_uuid(),
  client_id uuid not null references public.clients(id) on delete cascade,
  sensor_id uuid references public.client_sensors(id) on delete set null,
  reason text,
  start_date date not null default current_date,
  end_date date,
  operator_id uuid,
  archived boolean not null default false,
  created_at timestamptz not null default now()
);

create table public.shifts (
  id uuid primary key default gen_random_uuid(),
  operator_id uuid not null,
  shift_type text not null default 'Diurno',
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  next_operator_id uuid,
  message text
);

do $$ declare t text; begin
  foreach t in array array['clients','client_users','client_devices','client_sensors','client_events','bypasses','shifts'] loop
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
    execute format('grant all on public.%I to service_role', t);
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy "team read" on public.%I for select to authenticated using (true)', t);
    execute format('create policy "team insert" on public.%I for insert to authenticated with check (true)', t);
    execute format('create policy "team update" on public.%I for update to authenticated using (true)', t);
    execute format('create policy "team delete" on public.%I for delete to authenticated using (true)', t);
  end loop;
end $$;

create or replace function public.archive_month(_month date, _archived boolean default true) returns integer language plpgsql security definer set search_path = public as $$
declare n integer; m integer;
begin
  if not public.has_role(auth.uid(),'admin') then raise exception 'Apenas administradores'; end if;
  update public.client_events set archived = _archived where event_date >= date_trunc('month', _month)::date and event_date < (date_trunc('month', _month) + interval '1 month')::date;
  get diagnostics n = row_count;
  update public.bypasses set archived = _archived where end_date is not null and end_date >= date_trunc('month', _month)::date and end_date < (date_trunc('month', _month) + interval '1 month')::date;
  get diagnostics m = row_count;
  return n + m;
end $$;
revoke execute on function public.archive_month(date, boolean) from anon, public;
grant execute on function public.archive_month(date, boolean) to authenticated;