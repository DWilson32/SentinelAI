import type { Incident, Severity } from "@/lib/types";

// Equirectangular projection (plate carrée): longitude and latitude map
// linearly onto x and y. Chosen because it needs no projection library and no
// map tiles, and because every incident already carries lat/lon.
const MAP_W = 720;
const MAP_H = 360;

function project(latitude: number, longitude: number): { x: number; y: number } {
  const lon = Math.max(-180, Math.min(180, longitude));
  const lat = Math.max(-90, Math.min(90, latitude));
  return {
    x: ((lon + 180) / 360) * MAP_W,
    y: ((90 - lat) / 180) * MAP_H,
  };
}

// Incidents with no geography are stored as 0/0 by the ingestion layer, which
// is a real point in the Gulf of Guinea. Plotting them would invent a cluster
// that does not exist, so they are counted separately instead.
function hasRealCoordinates(incident: Incident): boolean {
  const { latitude: lat, longitude: lon } = incident;
  if (lat === null || lon === null || Number.isNaN(lat) || Number.isNaN(lon)) return false;
  if (lat === 0 && lon === 0) return false;
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}

const SEVERITY_FILL: Record<Severity, string> = {
  low: "#059669",
  medium: "#d97706",
  high: "#ea580c",
  critical: "#dc2626",
};

const SEVERITY_ORDER: Severity[] = ["low", "medium", "high", "critical"];

// Coarse continental outlines as [longitude, latitude] pairs, drawn through the
// same projection as the incidents so land and markers stay consistent. This is
// a schematic backdrop for orientation, not a survey coastline.
const LANDMASSES: [number, number][][] = [
  // North America
  [
    [-168, 66], [-156, 71], [-130, 70], [-110, 69], [-90, 70], [-75, 68], [-60, 58],
    [-52, 47], [-66, 44], [-70, 41], [-76, 35], [-80, 25], [-84, 30], [-90, 29],
    [-97, 26], [-105, 22], [-114, 31], [-124, 40], [-130, 50], [-140, 60], [-155, 58],
  ],
  // Greenland
  [[-45, 60], [-30, 63], [-22, 70], [-25, 78], [-35, 83], [-50, 82], [-58, 77], [-55, 68], [-48, 62]],
  // South America
  [
    [-81, 8], [-75, 10], [-60, 10], [-52, 5], [-44, -2], [-35, -6], [-38, -13],
    [-48, -25], [-58, -35], [-62, -40], [-65, -50], [-72, -54], [-75, -45],
    [-73, -35], [-71, -25], [-70, -18], [-76, -6], [-80, 0],
  ],
  // Africa
  [
    [-17, 15], [-10, 28], [0, 32], [10, 34], [20, 32], [32, 31], [35, 25], [38, 18],
    [43, 12], [51, 11], [48, 0], [40, -10], [35, -20], [32, -26], [25, -34],
    [18, -34], [12, -18], [9, -2], [3, 5], [-8, 5], [-14, 10],
  ],
  // Eurasia
  [
    [-10, 36], [-9, 43], [0, 49], [8, 54], [12, 57], [20, 55], [25, 60], [30, 65],
    [28, 70], [45, 68], [60, 70], [75, 73], [95, 76], [110, 74], [130, 71], [145, 68],
    [160, 66], [170, 62], [160, 58], [145, 55], [140, 45], [130, 42], [122, 38],
    [120, 30], [110, 20], [105, 10], [100, 5], [95, 15], [88, 20], [80, 10], [75, 8],
    [70, 22], [62, 25], [58, 20], [50, 25], [45, 38], [35, 36], [28, 38], [20, 40],
    [12, 42], [3, 42],
  ],
  // Japan
  [[130, 31], [136, 34], [141, 40], [142, 45], [145, 44], [141, 36], [138, 34], [132, 30]],
  // Maritime South-East Asia
  [[95, 5], [105, -2], [115, -5], [125, -3], [135, -3], [140, -2], [133, -6], [120, -9], [110, -8], [100, -3]],
  // Australia
  [
    [113, -22], [122, -18], [130, -12], [137, -12], [142, -11], [146, -19], [150, -25],
    [153, -28], [150, -37], [145, -38], [137, -35], [130, -32], [122, -34], [115, -34], [113, -26],
  ],
];

function toPath(ring: [number, number][]): string {
  return (
    ring
      .map(([lon, lat], index) => {
        const { x, y } = project(lat, lon);
        return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ") + " Z"
  );
}

function radiusFor(riskScore: number): number {
  const risk = Math.max(0, Math.min(100, riskScore));
  return 2 + (risk / 100) * 3.5;
}

export function CrisisMap({ incidents }: { incidents: Incident[] }) {
  const mappable = incidents.filter(hasRealCoordinates);
  const unmapped = incidents.length - mappable.length;

  // Draw the most severe last so they sit on top of the crowd.
  const ordered = [...mappable].sort((a, b) => a.risk_score - b.risk_score);

  const graticuleLon = [-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150];
  const graticuleLat = [-60, -30, 0, 30, 60];

  return (
    <section className="rounded-lg border border-line bg-panel p-4 shadow-soft">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold text-ink">Global Crisis Map</h2>
        <span className="text-sm text-muted">
          Equirectangular projection &middot; {mappable.length.toLocaleString()} plotted
        </span>
      </div>

      <div className="mt-4 overflow-hidden rounded-md border border-line bg-cyan-50">
        <svg
          viewBox={`0 0 ${MAP_W} ${MAP_H}`}
          className="block h-auto w-full"
          role="img"
          aria-label={`World map showing ${mappable.length} incidents positioned by reported latitude and longitude.`}
        >
          {graticuleLon.map((lon) => {
            const { x } = project(0, lon);
            return (
              <line key={`lon-${lon}`} x1={x} y1={0} x2={x} y2={MAP_H} stroke="#cbd5e1" strokeWidth={0.5} />
            );
          })}
          {graticuleLat.map((lat) => {
            const { y } = project(lat, 0);
            return (
              <line
                key={`lat-${lat}`}
                x1={0}
                y1={y}
                x2={MAP_W}
                y2={y}
                stroke={lat === 0 ? "#94a3b8" : "#cbd5e1"}
                strokeWidth={lat === 0 ? 0.9 : 0.5}
              />
            );
          })}

          {LANDMASSES.map((ring, index) => (
            <path key={`land-${index}`} d={toPath(ring)} fill="#e2e8f0" stroke="#94a3b8" strokeWidth={0.6} />
          ))}

          {ordered.map((incident) => {
            const { x, y } = project(incident.latitude, incident.longitude);
            const fill = SEVERITY_FILL[incident.severity] ?? SEVERITY_FILL.medium;
            return (
              <circle
                key={incident.id}
                cx={x}
                cy={y}
                r={radiusFor(incident.risk_score)}
                fill={fill}
                fillOpacity={0.68}
                stroke="#ffffff"
                strokeWidth={0.6}
              >
                <title>
                  {`${incident.severity.toUpperCase()} · ${incident.category} · risk ${incident.risk_score}\n${incident.location}\n${incident.latitude.toFixed(2)}, ${incident.longitude.toFixed(2)}`}
                </title>
              </circle>
            );
          })}
        </svg>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
        {SEVERITY_ORDER.map((severity) => (
          <span key={severity} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: SEVERITY_FILL[severity] }}
              aria-hidden="true"
            />
            {severity}
          </span>
        ))}
        <span className="ml-auto">Marker size scales with risk score</span>
      </div>

      {unmapped > 0 && (
        <p className="mt-2 text-xs text-muted">
          {unmapped.toLocaleString()} incident{unmapped === 1 ? "" : "s"} not shown &mdash; the news
          feed supplies no coordinates for them.
        </p>
      )}
    </section>
  );
}
