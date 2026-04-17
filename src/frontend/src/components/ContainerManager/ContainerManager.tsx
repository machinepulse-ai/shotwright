import { Container } from "../../types";
import "./ContainerManager.css";

interface ContainerManagerProps {
  containers: Container[];
  onStop: (id: string) => void;
}

export default function ContainerManager({ containers, onStop }: ContainerManagerProps) {
  if (containers.length === 0) return null;

  return (
    <div className="card container-manager">
      <h3>Containers</h3>
      <div className="container-list">
        {containers.map((c) => (
          <div key={c._id} className="container-item">
            <div className="container-info">
              <span className="mono">{c.docker_id.slice(0, 12)}</span>
              <span className={`status-badge status-${c.status}`}>{c.status}</span>
            </div>
            {c.status === "running" && (
              <button className="btn-danger btn-sm" onClick={() => onStop(c._id)}>
                Stop
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
