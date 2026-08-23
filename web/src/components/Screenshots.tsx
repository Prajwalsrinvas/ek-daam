import type { RunState } from "../runState";
import type { Universe } from "../types";

export default function Screenshots({
  state,
  registry,
}: {
  state: RunState;
  registry: Universe[];
}) {
  const display = new Map(registry.map((u) => [u.id, u.display]));
  const shots = state.order
    .map((id) => ({ id, shot: state.universes[id].screenshot }))
    .filter((entry): entry is { id: string; shot: NonNullable<typeof entry.shot> } => !!entry.shot);

  if (shots.length === 0) return null;

  return (
    <section className="space-y-2">
      <h3 className="font-semibold">Captures</h3>
      <div className="flex flex-wrap gap-4">
        {shots.map(({ id, shot }) => (
          <a key={id} href={shot.url} target="_blank" rel="noreferrer" className="block">
            <img
              src={shot.url}
              alt={`${display.get(id) ?? id} capture`}
              className="h-32 w-52 rounded border border-slate-200 object-cover"
            />
            <div className="mt-1 text-xs text-slate-500">
              {display.get(id) ?? id}
              {shot.placeholder && <span className="ml-1 text-amber-700">placeholder</span>}
            </div>
          </a>
        ))}
      </div>
    </section>
  );
}
