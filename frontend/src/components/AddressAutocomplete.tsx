import { useEffect, useRef, useState } from "react";
import { geocodeSearch } from "../api/client";
import type { GeocodeResult } from "../api/client";
import { useAuth } from "../state/AuthContext";

interface AddressAutocompleteProps {
  label: string;
  placeholder?: string;
  value: string;
  onSelect: (result: GeocodeResult) => void;
  required?: boolean;
}

export function AddressAutocomplete({
  label,
  placeholder = "Search address…",
  value,
  onSelect,
  required,
}: AddressAutocompleteProps) {
  const { token } = useAuth();
  const [query, setQuery] = useState(value);
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef(false);

  // Keep local query in sync when the parent resets the form
  useEffect(() => {
    setQuery(value);
  }, [value]);

  // Debounced search
  useEffect(() => {
    if (selectedRef.current) {
      selectedRef.current = false;
      return;
    }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (query.length < 2) {
      setResults([]);
      setOpen(false);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await geocodeSearch(token!, query);
        setResults(res);
        setOpen(res.length > 0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, token]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleSelect(result: GeocodeResult) {
    selectedRef.current = true;
    setQuery(result.display_name);
    setOpen(false);
    setResults([]);
    onSelect(result);
  }

  return (
    <div ref={containerRef} className="relative">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => results.length > 0 && setOpen(true)}
          placeholder={placeholder}
          required={required}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm pr-8 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 outline-none"
        />
        {loading && (
          <svg
            className="absolute right-2.5 top-2.5 w-4 h-4 text-gray-400 animate-spin"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
          </svg>
        )}
      </div>

      {open && results.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full bg-white rounded-lg border border-gray-200 shadow-lg max-h-56 overflow-y-auto">
          {results.map((r, i) => (
            <li key={i}>
              <button
                type="button"
                onMouseDown={(e) => { e.preventDefault(); handleSelect(r); }}
                className="w-full text-left px-3 py-2.5 text-sm text-gray-700 hover:bg-brand-50 hover:text-brand-700 transition-colors flex items-start gap-2"
              >
                <svg className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400" fill="currentColor" viewBox="0 0 16 16">
                  <path d="M8 0C5.24 0 3 2.24 3 5c0 3.75 5 11 5 11s5-7.25 5-11c0-2.76-2.24-5-5-5zm0 7.5A2.5 2.5 0 115.5 5 2.5 2.5 0 018 7.5z" />
                </svg>
                <span className="leading-snug">{r.display_name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
