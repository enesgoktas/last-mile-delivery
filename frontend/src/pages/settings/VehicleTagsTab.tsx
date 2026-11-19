import { useCallback, useState, type FormEvent } from "react";
import { useAuth } from "../../state/AuthContext";
import { useToast } from "../../state/ToastContext";
import { usePolling } from "../../hooks/usePolling";
import { LoadingSpinner } from "../../components/LoadingSpinner";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import {
  createVehicleTag,
  deleteVehicleTag,
  listVehicleTags,
  updateVehicleTag,
} from "../../api/client";
import type { VehicleTag } from "../../types";
import { SETTINGS_INPUT_CLASSNAME } from "./shared";

const PRESET_COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#ef4444",
  "#f97316", "#eab308", "#22c55e", "#14b8a6",
  "#3b82f6", "#64748b",
];

function ColorSwatch({ color, size = 16 }: { color: string; size?: number }) {
  return (
    <span
      className="inline-block rounded-full border border-black/10 flex-shrink-0"
      style={{ width: size, height: size, backgroundColor: color }}
    />
  );
}

function ColorPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (c: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {PRESET_COLORS.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            className="w-7 h-7 rounded-full border-2 transition-all"
            style={{
              backgroundColor: c,
              borderColor: value === c ? "#1e293b" : "transparent",
              outline: value === c ? "2px solid #1e293b" : "none",
              outlineOffset: 2,
            }}
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <ColorSwatch color={value} size={24} />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#6366f1"
          className={`${SETTINGS_INPUT_CLASSNAME} font-mono w-32`}
          maxLength={7}
        />
        <span className="text-xs text-slate-500">hex color</span>
      </div>
    </div>
  );
}

export function VehicleTagsTab() {
  const { token } = useAuth();
  const { addToast } = useToast();

  const fetchTags = useCallback(() => listVehicleTags(token!), [token]);
  const { data: tags, isLoading, refetch } = usePolling(fetchTags, 60_000);

  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState<VehicleTag | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [color, setColor] = useState(PRESET_COLORS[0]);
  const [editName, setEditName] = useState("");
  const [editColor, setEditColor] = useState(PRESET_COLORS[0]);

  function openCreate() {
    setName("");
    setColor(PRESET_COLORS[0]);
    setError(null);
    setShowCreate(true);
  }

  function openEdit(tag: VehicleTag) {
    setEditName(tag.name);
    setEditColor(tag.color);
    setError(null);
    setShowEdit(tag);
  }

  async function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await createVehicleTag(token!, { name: name.trim(), color });
      addToast("Tag created", "success");
      setShowCreate(false);
      refetch();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create tag");
    } finally {
      setSaving(false);
    }
  }

  async function handleEdit(e: FormEvent) {
    e.preventDefault();
    if (!showEdit || !editName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await updateVehicleTag(token!, showEdit.id, { name: editName.trim(), color: editColor });
      addToast("Tag updated", "success");
      setShowEdit(null);
      refetch();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update tag");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteVehicleTag(token!, id);
      addToast("Tag deleted", "success");
      refetch();
    } catch {
      addToast("Failed to delete tag", "error");
    } finally {
      setDeleteId(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-slate-800">Vehicle Tags</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Label couriers with tags, then require those tags on orders so the solver only assigns eligible vehicles.
          </p>
        </div>
        <button
          onClick={openCreate}
          className="px-4 py-2 text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-lg transition-colors"
        >
          + New Tag
        </button>
      </div>

      {isLoading ? (
        <LoadingSpinner />
      ) : !tags || tags.length === 0 ? (
        <div className="text-center py-12 text-slate-400 text-sm border border-dashed border-slate-200 rounded-lg">
          No vehicle tags yet. Create one to start labeling couriers and orders.
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden">
          {tags.map((tag) => (
            <div
              key={tag.id}
              className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors"
            >
              <ColorSwatch color={tag.color} size={18} />
              <span
                className="flex-1 text-sm font-medium px-2.5 py-0.5 rounded-full w-fit"
                style={{ backgroundColor: tag.color + "22", color: tag.color }}
              >
                {tag.name}
              </span>
              <span className="font-mono text-xs text-slate-400 ml-auto mr-4">{tag.color}</span>
              <button
                onClick={() => openEdit(tag)}
                className="text-xs text-slate-500 hover:text-slate-800 px-2 py-1 rounded hover:bg-slate-100 transition-colors"
              >
                Edit
              </button>
              <button
                onClick={() => setDeleteId(tag.id)}
                className="text-xs text-red-500 hover:text-red-700 px-2 py-1 rounded hover:bg-red-50 transition-colors"
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Create modal */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} title="New Vehicle Tag">
        <form onSubmit={handleCreate} className="space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Refrigerated, Hazmat, Oversized"
              className={SETTINGS_INPUT_CLASSNAME}
              required
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-2">Color</label>
            <ColorPicker value={color} onChange={setColor} />
          </div>
          {name.trim() && (
            <div className="pt-1">
              <span className="text-xs text-slate-500 mr-2">Preview:</span>
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium"
                style={{ backgroundColor: color + "22", color }}
              >
                <ColorSwatch color={color} size={10} />
                {name.trim()}
              </span>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setShowCreate(false)}
              className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !name.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-lg disabled:opacity-50 transition-colors"
            >
              {saving ? "Creating…" : "Create Tag"}
            </button>
          </div>
        </form>
      </Modal>

      {/* Edit modal */}
      <Modal
        open={!!showEdit}
        onClose={() => setShowEdit(null)}
        title={`Edit Tag — ${showEdit?.name ?? ""}`}
      >
        <form onSubmit={handleEdit} className="space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Name</label>
            <input
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              className={SETTINGS_INPUT_CLASSNAME}
              required
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-2">Color</label>
            <ColorPicker value={editColor} onChange={setEditColor} />
          </div>
          {editName.trim() && (
            <div className="pt-1">
              <span className="text-xs text-slate-500 mr-2">Preview:</span>
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium"
                style={{ backgroundColor: editColor + "22", color: editColor }}
              >
                <ColorSwatch color={editColor} size={10} />
                {editName.trim()}
              </span>
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={() => setShowEdit(null)}
              className="px-4 py-2 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-lg hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !editName.trim()}
              className="px-4 py-2 text-sm font-medium text-white bg-brand-600 hover:bg-brand-700 rounded-lg disabled:opacity-50 transition-colors"
            >
              {saving ? "Saving…" : "Save Changes"}
            </button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!deleteId}
        title="Delete tag?"
        message="This will remove the tag from all couriers and orders that reference it."
        confirmLabel="Delete"
        onConfirm={() => deleteId && handleDelete(deleteId)}
        onCancel={() => setDeleteId(null)}
      />
    </div>
  );
}
