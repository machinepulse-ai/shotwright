import { Container } from "../../types";
import { useI18n } from "../../i18n";
import "./ContainerManager.css";

interface ContainerManagerProps {
  containers: Container[];
  onStop: (id: string) => void;
}

export default function ContainerManager({ containers, onStop }: ContainerManagerProps) {
  const { copy } = useI18n();
  const containerStatusLabels = copy.status.container;

  if (containers.length === 0) return null;

  return (
    <div className="card container-manager">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{copy.container.eyebrow}</span>
          <h3>{copy.container.title}</h3>
        </div>
        <span className="panel-count">{containers.length}</span>
      </div>
      <div className="container-list">
        {containers.map((c) => (
          <div key={c._id} className="container-item">
            <div className="container-info">
              <div className="container-meta">
                <span className="container-label">{copy.container.runtimeLabel}</span>
                <span className="mono">{c.docker_id.slice(0, 12)}</span>
              </div>
              <span className={`status-badge status-${c.status}`}>{containerStatusLabels[c.status]}</span>
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
