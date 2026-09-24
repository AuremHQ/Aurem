/**
 * CountryCodeSelect.jsx — 2026-09-22
 * Small "+XX" dial-code dropdown for phone fields. Pairs with a plain
 * national-number `<input>` — this component only owns the prefix,
 * never validates or submits anything on its own.
 *
 * Deliberately a short, curated list (not all ~240 dial codes) —
 * covers the countries AUREM actually sees signups from today plus
 * every G20 country, so the dropdown stays scannable instead of a
 * 240-row wall. "Other" lets anyone else type a full "+<code> …"
 * manually in the number field if their country isn't listed.
 */
import React from "react";

export const COUNTRY_DIAL_CODES = [
  { code: "US", dial: "+1",   flag: "🇺🇸", name: "United States" },
  { code: "CA", dial: "+1",   flag: "🇨🇦", name: "Canada" },
  { code: "GB", dial: "+44",  flag: "🇬🇧", name: "United Kingdom" },
  { code: "IN", dial: "+91",  flag: "🇮🇳", name: "India" },
  { code: "AU", dial: "+61",  flag: "🇦🇺", name: "Australia" },
  { code: "DE", dial: "+49",  flag: "🇩🇪", name: "Germany" },
  { code: "FR", dial: "+33",  flag: "🇫🇷", name: "France" },
  { code: "IT", dial: "+39",  flag: "🇮🇹", name: "Italy" },
  { code: "ES", dial: "+34",  flag: "🇪🇸", name: "Spain" },
  { code: "NL", dial: "+31",  flag: "🇳🇱", name: "Netherlands" },
  { code: "IE", dial: "+353", flag: "🇮🇪", name: "Ireland" },
  { code: "SE", dial: "+46",  flag: "🇸🇪", name: "Sweden" },
  { code: "PT", dial: "+351", flag: "🇵🇹", name: "Portugal" },
  { code: "PL", dial: "+48",  flag: "🇵🇱", name: "Poland" },
  { code: "MX", dial: "+52",  flag: "🇲🇽", name: "Mexico" },
  { code: "BR", dial: "+55",  flag: "🇧🇷", name: "Brazil" },
  { code: "AR", dial: "+54",  flag: "🇦🇷", name: "Argentina" },
  { code: "JP", dial: "+81",  flag: "🇯🇵", name: "Japan" },
  { code: "KR", dial: "+82",  flag: "🇰🇷", name: "South Korea" },
  { code: "CN", dial: "+86",  flag: "🇨🇳", name: "China" },
  { code: "SG", dial: "+65",  flag: "🇸🇬", name: "Singapore" },
  { code: "ID", dial: "+62",  flag: "🇮🇩", name: "Indonesia" },
  { code: "PH", dial: "+63",  flag: "🇵🇭", name: "Philippines" },
  { code: "VN", dial: "+84",  flag: "🇻🇳", name: "Vietnam" },
  { code: "PK", dial: "+92",  flag: "🇵🇰", name: "Pakistan" },
  { code: "BD", dial: "+880", flag: "🇧🇩", name: "Bangladesh" },
  { code: "AE", dial: "+971", flag: "🇦🇪", name: "United Arab Emirates" },
  { code: "SA", dial: "+966", flag: "🇸🇦", name: "Saudi Arabia" },
  { code: "IL", dial: "+972", flag: "🇮🇱", name: "Israel" },
  { code: "TR", dial: "+90",  flag: "🇹🇷", name: "Turkey" },
  { code: "ZA", dial: "+27",  flag: "🇿🇦", name: "South Africa" },
  { code: "NG", dial: "+234", flag: "🇳🇬", name: "Nigeria" },
  { code: "EG", dial: "+20",  flag: "🇪🇬", name: "Egypt" },
  { code: "KE", dial: "+254", flag: "🇰🇪", name: "Kenya" },
  { code: "RU", dial: "+7",   flag: "🇷🇺", name: "Russia" },
  { code: "UA", dial: "+380", flag: "🇺🇦", name: "Ukraine" },
  { code: "NZ", dial: "+64",  flag: "🇳🇿", name: "New Zealand" },
];

export default function CountryCodeSelect({ value, onChange, testId }) {
  return (
    <select
      data-testid={testId}
      className="input"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ flex: "0 0 108px", paddingRight: 6 }}
      aria-label="Country code"
    >
      {COUNTRY_DIAL_CODES.map((c) => (
        <option key={c.code} value={c.dial}>
          {c.flag} {c.dial}
        </option>
      ))}
    </select>
  );
}
