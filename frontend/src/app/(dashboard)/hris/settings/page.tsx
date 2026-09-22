'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Lock, Plus, Trash2, Unlock, X } from 'lucide-react';

interface SettingsData {
  can_edit: boolean;
  heads: string[];
  recipients: string[];
  locked: Record<string, boolean>;
  rules: Rule[];
  team: TeamMember[];
  people: Person[];
}

interface Rule {
  category: string;
  label: string;
  months_before: number;
  is_active: boolean;
}

interface RuleDraft {
  category: string;
  label: string;
  months_before: number;
  is_active: boolean;
}

interface TeamMember {
  user_id: number;
  name: string;
  email: string;
  role_code: string;
  role_label: string;
  assignment_id: number;
}

interface Person {
  employee_id: number;
  name: string;
  department: string;
  company: string;
  is_controller: boolean;
  is_expatriate: boolean;
}

type RoleCode = 'HR_MANAGER' | 'HR_VIEWER';
type LockKey = 'hr_heads' | 'contract_reminder_recipients';

const getErrorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong';

function formatLoginDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

interface ChipEditorProps {
  items: string[];
  onChange: (items: string[]) => void;
  locked: boolean;
  disabled: boolean;
  onSave: () => void;
  onToggleLock: () => void;
  lockMessage: string;
  message?: string;
  error?: string;
  placeholder?: string;
}

function ChipEditor({
  items,
  onChange,
  locked,
  disabled,
  onSave,
  onToggleLock,
  lockMessage,
  message,
  error,
  placeholder = 'Enter an email',
}: ChipEditorProps) {
  const [input, setInput] = useState('');

  const addItem = () => {
    const value = input.trim();
    if (!value || items.includes(value)) return;
    onChange([...items, value]);
    setInput('');
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <span
            key={item}
            className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1 text-sm text-gray-700"
          >
            {item}
            <button
              type="button"
              onClick={() => onChange(items.filter((current) => current !== item))}
              disabled={locked || disabled}
              className="text-gray-400 hover:text-gray-700"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={placeholder}
          disabled={locked || disabled}
          className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
        />
        <Button
          type="button"
          variant="outline"
          onClick={addItem}
          disabled={locked || disabled || !input.trim()}
        >
          <Plus className="mr-1 h-4 w-4" />
          Add
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" onClick={onSave} disabled={locked || disabled}>
          Save
        </Button>
        <Button type="button" variant="outline" onClick={onToggleLock} disabled={disabled}>
          {locked ? <Lock className="mr-1 h-4 w-4" /> : <Unlock className="mr-1 h-4 w-4" />}
          {locked ? 'Locked — unlock to change' : 'Lock this setting'}
        </Button>
      </div>

      {message && <p className="text-sm text-emerald-600">{message}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}
      {locked && <p className="text-sm text-gray-500">{lockMessage}</p>}
    </div>
  );
}

export default function SettingsPage() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [headsDraft, setHeadsDraft] = useState<string[]>([]);
  const [recipientsDraft, setRecipientsDraft] = useState<string[]>([]);
  const [rulesDraft, setRulesDraft] = useState<RuleDraft[]>([]);

  const [headsMessage, setHeadsMessage] = useState('');
  const [headsError, setHeadsError] = useState('');
  const [headsSaving, setHeadsSaving] = useState(false);

  const [recipientsMessage, setRecipientsMessage] = useState('');
  const [recipientsError, setRecipientsError] = useState('');
  const [recipientsSaving, setRecipientsSaving] = useState(false);

  const [lockSaving, setLockSaving] = useState<LockKey | null>(null);
  const [lockErrors, setLockErrors] = useState<Record<LockKey, string>>({
    hr_heads: '',
    contract_reminder_recipients: '',
  });

  const [teamEmail, setTeamEmail] = useState('');
  const [teamRole, setTeamRole] = useState<RoleCode>('HR_MANAGER');
  const [teamSaving, setTeamSaving] = useState(false);
  const [teamMessage, setTeamMessage] = useState('');
  const [teamError, setTeamError] = useState('');
  const [removingTeamId, setRemovingTeamId] = useState<number | null>(null);

  const [ruleSavingCategory, setRuleSavingCategory] = useState<string | null>(null);
  const [ruleMessages, setRuleMessages] = useState<Record<string, string>>({});
  const [ruleErrors, setRuleErrors] = useState<Record<string, string>>({});

  const [peopleSearch, setPeopleSearch] = useState('');
  const [flagMessage, setFlagMessage] = useState('');
  const [flagError, setFlagError] = useState('');
  const [savingFlags, setSavingFlags] = useState<Record<string, boolean>>({});

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<SettingsData>('/hris/settings/');
      setData(response);
      setHeadsDraft(response.heads);
      setRecipientsDraft(response.recipients);
      setRulesDraft(response.rules.map((rule) => ({ ...rule })));
    } catch (err) {
      setError(getErrorMessage(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const postJson = async <T,>(path: string, body: unknown): Promise<T> => {
    return apiFetch<T>(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  };

  const refreshAfterChange = async () => {
    const response = await apiFetch<SettingsData>('/hris/settings/');
    setData(response);
    setHeadsDraft(response.heads);
    setRecipientsDraft(response.recipients);
    setRulesDraft(response.rules.map((rule) => ({ ...rule })));
  };

  const saveHeads = async () => {
    if (!data) return;
    setHeadsSaving(true);
    setHeadsMessage('');
    setHeadsError('');
    try {
      await postJson('/hris/settings/update/', { key: 'hr_heads', value: headsDraft });
      setHeadsMessage('Saved. A copy has been sent to the CFO.');
      await refreshAfterChange();
    } catch (err) {
      setHeadsError(getErrorMessage(err));
    } finally {
      setHeadsSaving(false);
    }
  };

  const saveRecipients = async () => {
    if (!data) return;
    setRecipientsSaving(true);
    setRecipientsMessage('');
    setRecipientsError('');
    try {
      await postJson('/hris/settings/update/', {
        key: 'contract_reminder_recipients',
        value: recipientsDraft,
      });
      setRecipientsMessage('Saved. A copy has been sent to the CFO.');
      await refreshAfterChange();
    } catch (err) {
      setRecipientsError(getErrorMessage(err));
    } finally {
      setRecipientsSaving(false);
    }
  };

  const toggleLock = async (key: LockKey) => {
    if (!data) return;
    const nextLocked = !data.locked[key];
    setLockSaving(key);
    setLockErrors((prev) => ({ ...prev, [key]: '' }));
    try {
      await postJson('/hris/settings/lock/', { key, locked: nextLocked });
      await refreshAfterChange();
    } catch (err) {
      setLockErrors((prev) => ({ ...prev, [key]: getErrorMessage(err) }));
    } finally {
      setLockSaving(null);
    }
  };

  const addTeamMember = async () => {
    if (!data?.can_edit || !teamEmail.trim()) return;
    setTeamSaving(true);
    setTeamMessage('');
    setTeamError('');
    try {
      await postJson('/hris/settings/team/add/', { email: teamEmail.trim(), role_code: teamRole });
      setTeamMessage('Person added');
      setTeamEmail('');
      await refreshAfterChange();
    } catch (err) {
      setTeamError(getErrorMessage(err));
    } finally {
      setTeamSaving(false);
    }
  };

  const removeTeamMember = async (member: TeamMember) => {
    if (!data?.can_edit) return;
    if (!window.confirm(`Remove ${member.name || member.email}?`)) return;

    setRemovingTeamId(member.assignment_id);
    setTeamMessage('');
    setTeamError('');
    try {
      await postJson('/hris/settings/team/remove/', { assignment_id: member.assignment_id });
      setTeamMessage('Removed');
      await refreshAfterChange();
    } catch (err) {
      setTeamError(getErrorMessage(err));
    } finally {
      setRemovingTeamId(null);
    }
  };

  const updateRuleDraft = (
    category: string,
    patch: Partial<Pick<RuleDraft, 'months_before' | 'is_active'>>
  ) => {
    setRulesDraft((prev) =>
      prev.map((rule) => (rule.category === category ? { ...rule, ...patch } : rule))
    );
  };

  const saveRule = async (draft: RuleDraft) => {
    if (!data?.can_edit) return;
    setRuleSavingCategory(draft.category);
    setRuleMessages((prev) => ({ ...prev, [draft.category]: '' }));
    setRuleErrors((prev) => ({ ...prev, [draft.category]: '' }));

    try {
      await postJson('/hris/settings/rule/', {
        category: draft.category,
        months_before: draft.months_before,
        is_active: draft.is_active,
      });
      setRuleMessages((prev) => ({ ...prev, [draft.category]: 'Saved' }));
      await refreshAfterChange();
    } catch (err) {
      setRuleErrors((prev) => ({ ...prev, [draft.category]: getErrorMessage(err) }));
    } finally {
      setRuleSavingCategory(null);
    }
  };

  const togglePersonFlag = async (person: Person, flag: 'is_controller' | 'is_expatriate') => {
    if (!data?.can_edit) return;

    const nextValue = !person[flag];
    const flagKey = `${person.employee_id}-${flag}`;
    setSavingFlags((prev) => ({ ...prev, [flagKey]: true }));
    setFlagMessage('');
    setFlagError('');

    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        people: prev.people.map((current) => {
          if (current.employee_id !== person.employee_id) return current;
          return flag === 'is_controller'
            ? { ...current, is_controller: nextValue }
            : { ...current, is_expatriate: nextValue };
        }),
      };
    });

    try {
      await postJson('/hris/settings/person-flags/', {
        employee_id: person.employee_id,
        ...(flag === 'is_controller'
          ? { is_controller: nextValue }
          : { is_expatriate: nextValue }),
      });
      setFlagMessage('Updated. A copy has been sent to the CFO.');
    } catch (err) {
      setData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          people: prev.people.map((current) => {
            if (current.employee_id !== person.employee_id) return current;
            return flag === 'is_controller'
              ? { ...current, is_controller: person[flag] }
              : { ...current, is_expatriate: person[flag] };
          }),
        };
      });
      setFlagError(getErrorMessage(err));
    } finally {
      setSavingFlags((prev) => ({ ...prev, [flagKey]: false }));
    }
  };

  const filteredPeople = useMemo(() => {
    const people = data?.people ?? [];
    const term = peopleSearch.trim().toLowerCase();
    if (!term) return people;

    return people.filter((person) =>
      `${person.name} ${person.department} ${person.company} ${person.employee_id}`
        .toLowerCase()
        .includes(term)
    );
  }, [data?.people, peopleSearch]);

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar title="HR Settings" />

      <main className="mx-auto max-w-5xl space-y-6 px-4 py-7 sm:px-6">
        <p className="text-sm text-gray-500">
          Run by Human Resources. The CFO gets a copy of every change.
        </p>

        {!data?.can_edit && !loading && !error && (
          <div className="rounded-lg border border-gray-200 bg-gray-100 px-4 py-3 text-sm text-gray-700">
            View only — only HR heads can change these settings.
          </div>
        )}

        {loading ? (
          <p className="py-10 text-center text-sm text-gray-500">Loading settings…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
        ) : data ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle>HR team</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <ul className="divide-y divide-gray-100">
                  {data.team.map((member) => (
                    <li key={member.assignment_id} className="flex items-center justify-between gap-4 py-3">
                      <div>
                        <p className="font-semibold text-[#0D1B2A]">{member.name || member.email}</p>
                        <p className="text-sm text-gray-500">{member.email}</p>
                        <p className="mt-1 text-[11px] uppercase tracking-[0.12em] text-gray-400">
                          {member.role_label}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => removeTeamMember(member)}
                        disabled={!data.can_edit || removingTeamId === member.assignment_id}
                      >
                        <Trash2 className="mr-1 h-4 w-4" />
                        Remove
                      </Button>
                    </li>
                  ))}
                </ul>

                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    type="email"
                    value={teamEmail}
                    onChange={(event) => setTeamEmail(event.target.value)}
                    placeholder="Email address"
                    disabled={!data.can_edit}
                    className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                  />
                  <select
                    value={teamRole}
                    onChange={(event) => setTeamRole(event.target.value as RoleCode)}
                    disabled={!data.can_edit}
                    className="rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                  >
                    <option value="HR_MANAGER">Full HR incl. payroll</option>
                    <option value="HR_VIEWER">View only, no pay</option>
                  </select>
                  <Button
                    type="button"
                    onClick={addTeamMember}
                    disabled={!data.can_edit || !teamEmail.trim() || teamSaving}
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    Add person
                  </Button>
                </div>

                {teamMessage && <p className="text-sm text-emerald-600">{teamMessage}</p>}
                {teamError && <p className="text-sm text-red-600">{teamError}</p>}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>HR heads</CardTitle>
              </CardHeader>
              <CardContent>
                <ChipEditor
                  items={headsDraft}
                  onChange={setHeadsDraft}
                  locked={data.locked.hr_heads ?? false}
                  disabled={!data.can_edit || headsSaving || lockSaving === 'hr_heads'}
                  onSave={saveHeads}
                  onToggleLock={() => toggleLock('hr_heads')}
                  lockMessage="Locked — unlock to change."
                  message={headsMessage}
                  error={headsError || lockErrors.hr_heads}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Who receives contract reminders</CardTitle>
              </CardHeader>
              <CardContent>
                <ChipEditor
                  items={recipientsDraft}
                  onChange={setRecipientsDraft}
                  locked={data.locked.contract_reminder_recipients ?? false}
                  disabled={!data.can_edit || recipientsSaving || lockSaving === 'contract_reminder_recipients'}
                  onSave={saveRecipients}
                  onToggleLock={() => toggleLock('contract_reminder_recipients')}
                  lockMessage="Locked — unlock to change."
                  message={recipientsMessage}
                  error={recipientsError || lockErrors.contract_reminder_recipients}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Reminder timing</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {rulesDraft.map((rule) => (
                  <div key={rule.category} className="grid grid-cols-1 gap-3 sm:grid-cols-4 sm:items-center">
                    <div className="sm:col-span-2">
                      <p className="font-semibold text-[#0D1B2A]">{rule.label}</p>
                      {!rule.is_active && <p className="text-xs text-gray-400">Off</p>}
                    </div>

                    <input
                      type="number"
                      min={1}
                      max={24}
                      value={rule.months_before}
                      onChange={(event) =>
                        updateRuleDraft(rule.category, { months_before: Number(event.target.value) })
                      }
                      disabled={!data.can_edit}
                      className="rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                    />

                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={rule.is_active}
                        onClick={() => updateRuleDraft(rule.category, { is_active: !rule.is_active })}
                        disabled={!data.can_edit}
                        className={`relative h-6 w-10 rounded-full p-1 transition ${
                          rule.is_active ? 'bg-[#0D1B2A]' : 'bg-gray-300'
                        }`}
                      >
                        <span
                          className={`block h-4 w-4 rounded-full bg-white transition ${
                            rule.is_active ? 'translate-x-4' : ''
                          }`}
                        />
                      </button>
                      <Button
                        type="button"
                        onClick={() => saveRule(rule)}
                        disabled={!data.can_edit || ruleSavingCategory === rule.category}
                      >
                        {ruleSavingCategory === rule.category ? 'Saving...' : 'Save'}
                      </Button>
                    </div>

                    {ruleMessages[rule.category] && (
                      <p className="text-sm text-emerald-600 sm:col-span-4">{ruleMessages[rule.category]}</p>
                    )}
                    {ruleErrors[rule.category] && (
                      <p className="text-sm text-red-600 sm:col-span-4">{ruleErrors[rule.category]}</p>
                    )}
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Controllers & expatriates</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <input
                  type="search"
                  value={peopleSearch}
                  onChange={(event) => setPeopleSearch(event.target.value)}
                  placeholder="Search by name, department, company or employee number"
                  disabled={!data.can_edit}
                  className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                />

                {flagMessage && <p className="text-sm text-emerald-600">{flagMessage}</p>}
                {flagError && <p className="text-sm text-red-600">{flagError}</p>}

                <div className="max-h-[400px] divide-y divide-gray-100 overflow-y-auto">
                  {filteredPeople.length === 0 ? (
                    <p className="py-4 text-sm text-gray-500">No people found.</p>
                  ) : (
                    filteredPeople.map((person) => {
                      const controllerKey = `${person.employee_id}-is_controller`;
                      const expatKey = `${person.employee_id}-is_expatriate`;
                      return (
                        <div
                          key={person.employee_id}
                          className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                        >
                          <div>
                            <p className="font-semibold text-[#0D1B2A]">{person.name}</p>
                            <p className="text-sm text-gray-500">
                              {person.department} · {person.company}
                            </p>
                          </div>

                          <div className="flex gap-5">
                            <label className="inline-flex items-center gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={person.is_controller}
                                disabled={!data.can_edit || savingFlags[controllerKey]}
                                onChange={() => togglePersonFlag(person, 'is_controller')}
                                className="rounded border-gray-300"
                              />
                              Controller
                            </label>
                            <label className="inline-flex items-center gap-2 text-sm">
                              <input
                                type="checkbox"
                                checked={person.is_expatriate}
                                disabled={!data.can_edit || savingFlags[expatKey]}
                                onChange={() => togglePersonFlag(person, 'is_expatriate')}
                                className="rounded border-gray-300"
                              />
                              Expatriate
                            </label>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </CardContent>
            </Card>

            <SystemsCard canEdit={data.can_edit} />
            <LoginWithoutPayrollCard canEdit={data.can_edit} />
          </>
        ) : null}
      </main>
    </div>
  );
}

interface SystemAccessRow {
  department: string;
  systems: string[];
  note: string;
}

interface SystemAccessResponse {
  rows: SystemAccessRow[];
}

function SystemChipEditor({
  systems,
  canEdit,
  onChange,
}: {
  systems: string[];
  canEdit: boolean;
  onChange: (systems: string[]) => void;
}) {
  const [input, setInput] = useState('');

  const addSystem = () => {
    const value = input.trim();
    if (!value || systems.includes(value)) return;
    onChange([...systems, value]);
    setInput('');
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {systems.map((system) => (
          <span
            key={system}
            className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1 text-sm text-gray-700"
          >
            {system}
            <button
              type="button"
              onClick={() => onChange(systems.filter((current) => current !== system))}
              disabled={!canEdit}
              className="text-gray-400 hover:text-gray-700"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={!canEdit}
          placeholder="Add a system"
          className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
        />
        <Button
          type="button"
          variant="outline"
          onClick={addSystem}
          disabled={!canEdit || !input.trim()}
        >
          <Plus className="mr-1 h-4 w-4" />
          Add
        </Button>
      </div>
    </div>
  );
}

function SystemsCard({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<SystemAccessRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [newDepartment, setNewDepartment] = useState('');
  const [savingDepartment, setSavingDepartment] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  const loadSystems = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<SystemAccessResponse>('/hris/settings/systems/');
      setRows(response.rows);
    } catch (err) {
      setError(getErrorMessage(err));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSystems();
  }, [loadSystems]);

  const updateSystems = (department: string, systems: string[]) => {
    setRows((prev) =>
      prev.map((row) => (row.department === department ? { ...row, systems } : row))
    );
  };

  const updateNote = (department: string, note: string) => {
    setRows((prev) =>
      prev.map((row) => (row.department === department ? { ...row, note } : row))
    );
  };

  const addDepartment = () => {
    const value = newDepartment.trim();
    if (!value || rows.some((row) => row.department.toLowerCase() === value.toLowerCase())) return;
    setRows((prev) => [...prev, { department: value, systems: [], note: '' }]);
    setNewDepartment('');
  };

  const saveRow = async (row: SystemAccessRow) => {
    if (!canEdit) return;
    setSavingDepartment(row.department);
    setMessages((prev) => ({ ...prev, [row.department]: '' }));
    setErrors((prev) => ({ ...prev, [row.department]: '' }));
    try {
      await apiFetch('/hris/settings/systems/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ department: row.department, systems: row.systems, note: row.note }),
      });
      setMessages((prev) => ({ ...prev, [row.department]: 'Saved' }));
      await loadSystems();
    } catch (err) {
      setErrors((prev) => ({ ...prev, [row.department]: getErrorMessage(err) }));
    } finally {
      setSavingDepartment(null);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Systems each role needs</CardTitle>
        <p className="text-sm text-gray-500">IT is emailed these for every new joiner.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading && rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-gray-500">Loading systems…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
        ) : (
          <>
            <div className="space-y-4">
              {rows.map((row) => (
                <div key={row.department} className="rounded-lg border border-gray-200 p-4 space-y-3">
                  <p className="font-semibold text-[#0D1B2A]">
                    {row.department === '*' ? 'Every new joiner' : row.department}
                  </p>
                  <SystemChipEditor
                    systems={row.systems}
                    canEdit={canEdit}
                    onChange={(systems) => updateSystems(row.department, systems)}
                  />
                  <input
                    value={row.note}
                    onChange={(event) => updateNote(row.department, event.target.value)}
                    disabled={!canEdit}
                    placeholder="Note (optional)"
                    className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                  />
                  <Button
                    type="button"
                    onClick={() => saveRow(row)}
                    disabled={!canEdit || savingDepartment === row.department}
                  >
                    {savingDepartment === row.department ? 'Saving...' : 'Save'}
                  </Button>
                  {messages[row.department] && (
                    <p className="text-sm text-emerald-600">{messages[row.department]}</p>
                  )}
                  {errors[row.department] && (
                    <p className="text-sm text-red-600">{errors[row.department]}</p>
                  )}
                </div>
              ))}
            </div>

            <div className="flex gap-2">
              <input
                value={newDepartment}
                onChange={(event) => setNewDepartment(event.target.value)}
                disabled={!canEdit}
                placeholder="Department name"
                className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
              />
              <Button
                type="button"
                variant="outline"
                onClick={addDepartment}
                disabled={!canEdit || !newDepartment.trim()}
              >
                <Plus className="mr-1 h-4 w-4" />
                Add a department
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

interface LoginWithoutPayrollRow {
  user_id: number;
  username: string;
  email: string;
  name: string;
  last_login: string | null;
  classification: string;
}

interface LoginWithoutPayrollResponse {
  rows: LoginWithoutPayrollRow[];
}

type ClassificationKind = 'staff' | 'partner' | 'service' | 'close';

function normalizeClassification(value: string): ClassificationKind {
  if (value === 'staff' || value === 'service' || value === 'close' || value === 'partner') {
    return value;
  }
  return 'partner';
}

function LoginWithoutPayrollCard({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<LoginWithoutPayrollRow[]>([]);
  const [selections, setSelections] = useState<Record<number, ClassificationKind>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [savingUserId, setSavingUserId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Record<number, string>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});

  const loadLogins = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiFetch<LoginWithoutPayrollResponse>('/hris/logins-without-payroll/');
      setRows(response.rows);
      const initialSelections: Record<number, ClassificationKind> = {};
      for (const row of response.rows) {
        initialSelections[row.user_id] = normalizeClassification(row.classification);
      }
      setSelections(initialSelections);
    } catch (err) {
      setError(getErrorMessage(err));
      setRows([]);
      setSelections({});
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLogins();
  }, [loadLogins]);

  const saveClassification = async (row: LoginWithoutPayrollRow) => {
    if (!canEdit) return;
    const kind = selections[row.user_id] ?? 'partner';
    if (kind === 'close') {
      const name = row.name || row.username || row.email;
      if (!window.confirm(`Close the login for ${name}?`)) return;
    }

    setSavingUserId(row.user_id);
    setMessages((prev) => ({ ...prev, [row.user_id]: '' }));
    setErrors((prev) => ({ ...prev, [row.user_id]: '' }));
    try {
      await apiFetch(`/hris/logins-without-payroll/${row.user_id}/classify/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind, note: '' }),
      });
      setMessages((prev) => ({ ...prev, [row.user_id]: 'Saved' }));
      await loadLogins();
    } catch (err) {
      setErrors((prev) => ({ ...prev, [row.user_id]: getErrorMessage(err) }));
    } finally {
      setSavingUserId(null);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle>Omni logins without a payroll record</CardTitle>
        </div>
        <span className="rounded-full bg-[#F4A623] px-2.5 py-0.5 text-xs font-semibold text-white">
          {rows.length}
        </span>
      </CardHeader>
      <CardContent>
        {loading && rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-gray-500">Loading logins…</p>
        ) : error ? (
          <p className="rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-sm text-red-600">{error}</p>
        ) : rows.length === 0 ? (
          <p className="py-4 text-center text-sm text-gray-500">
            No logins without a payroll record.
          </p>
        ) : (
          <div className="divide-y divide-gray-100">
            {rows.map((row) => (
              <div key={row.user_id} className="py-4">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1.2fr_1fr_0.8fr_1fr_auto] sm:items-center">
                  <div>
                    <p className="font-semibold text-[#0D1B2A]">{row.name || row.username}</p>
                    <p className="text-sm text-gray-500">{row.email}</p>
                  </div>

                  <div className="text-sm text-gray-600">{formatLoginDate(row.last_login)}</div>

                  <div>
                    <span className="inline-flex rounded-full border border-gray-200 bg-gray-50 px-2.5 py-0.5 text-xs font-medium text-gray-600">
                      {row.classification || 'Unknown'}
                    </span>
                  </div>

                  <select
                    value={selections[row.user_id] ?? 'partner'}
                    onChange={(event) =>
                      setSelections((prev) => ({
                        ...prev,
                        [row.user_id]: event.target.value as ClassificationKind,
                      }))
                    }
                    disabled={!canEdit}
                    className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-[#0D1B2A]"
                  >
                    <option value="staff">Our staff — fix payroll record</option>
                    <option value="partner">Partner or external — keep</option>
                    <option value="service">Service account — keep</option>
                    <option value="close">Unknown — close the login</option>
                  </select>

                  <Button
                    type="button"
                    onClick={() => saveClassification(row)}
                    disabled={!canEdit || savingUserId === row.user_id}
                  >
                    {savingUserId === row.user_id ? 'Saving...' : 'Save'}
                  </Button>
                </div>

                {messages[row.user_id] && (
                  <p className="mt-2 text-sm text-emerald-600">{messages[row.user_id]}</p>
                )}
                {errors[row.user_id] && (
                  <p className="mt-2 text-sm text-red-600">{errors[row.user_id]}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
