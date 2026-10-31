import { useBackendHealth } from "../hooks/useBackendHealth";

export function HealthStatus() {
  const { state, checks } = useBackendHealth();

  if (state === "loading") return <p>Checking backend...</p>;

  return (
    <section>
      <p>{state === "ready" ? "Backend ready" : "Backend unavailable"}</p>
      {checks && (
        <ul>
          {Object.entries(checks).map(([name, status]) => (
            <li key={name}>{`${name}: ${status}`}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
