"use client";

import {memoryFields, type MemoryFormKind} from "../../lib/typed-memory-form";

/** Render explicit typed fields without inferring locations, timezones or coordinate systems. */
export function TypedMemoryFields({kind, values, onChange}: {
  kind: MemoryFormKind; values: Record<string,string>; onChange: (values: Record<string,string>) => void;
}) {
  return <>{memoryFields[kind].map(field => <label key={field.key}>{field.label}{field.optional ? "（可选）" : ""}
    {field.options ? <select required value={values[field.key] ?? ""}
      onChange={event => onChange({...values,[field.key]:event.target.value})}>
      <option value="">请选择</option>{field.options.map(([value,label]) => <option key={value} value={value}>{label}</option>)}
    </select> : <input type={field.type ?? "text"} step={field.type === "number" ? "any" : undefined}
      required={!field.optional} value={values[field.key] ?? ""}
      onChange={event => onChange({...values,[field.key]:event.target.value})} />}
  </label>)}</>;
}
