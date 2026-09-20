export type Session = {
  email: string;
  displayName: string;
  role: "DONOR" | "OPERATOR" | "VERIFIER" | "ADMIN";
  organization: { id: string; name: string };
  walletAddress: string | null;
};
