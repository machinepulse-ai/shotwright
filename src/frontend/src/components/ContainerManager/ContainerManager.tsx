import { Container } from "../../types";
import { useI18n } from "../../i18n";
import "./ContainerManager.css";

interface ContainerManagerProps {
  containers: Container[];
  onStop: (id: string) => void;
}

function formatContainerTime(value: string, locale: string, fallback: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return fallback;
  }

  return parsed.toLocaleString(locale, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export default function ContainerManager({ containers, onStop }: ContainerManagerProps) {
  const { copy, locale } = useI18n();
  const containerStatusLabels = copy.status.container;

  if (containers.length === 0) return null;

  return (
    <div className="card container-manager">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{copy.container.eyebrow}</span>
          <h3>{copy.container.title}</h3>
          <p className="panel-description container-panel-description">{copy.container.description}</p>
        </div>
        <span className="panel-count">{containers.length}</span>
      </div>
      <div className="container-list">
        {containers.map((c) => (
          <div key={c._id} className={`container-item is-${c.status}`}>
            <div className="container-info">
              <div className="container-item-topline">
                <div className="container-runtime-pill-group">
                  <span className="container-runtime-pill">{copy.container.runtimeLabel}</span>
                  <span className="container-image-label">{copy.container.imageLabel}</span>
                </div>
                <span className={`status-badge status-${c.status}`}>{containerStatusLabels[c.status]}</span>
              </div>
              <div className="container-meta">
                <div className="container-image">{c.image}</div>
                <dl className="container-fact-grid">
                  <div className="container-fact">
                    <dt>{copy.container.dockerIdLabel}</dt>
                    <dd className="mono">{c.docker_id.slice(0, 12)}</dd>
                  </div>
                  <div className="container-fact">
                    <dt>{copy.container.startedLabel}</dt>
                    <dd>{formatContainerTime(c.created_at, locale, copy.common.notStarted)}</dd>
                  </div>
                </dl>
              </div>
            </div>
            {c.status === "running" && (
              <button className="btn-danger btn-sm" onClick={() => onStop(c._id)}>
                {copy.container.stop}
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
