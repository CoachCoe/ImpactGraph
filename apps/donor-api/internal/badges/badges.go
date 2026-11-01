// Package badges is the badge catalog and earn engine (PRD §6.9, Appendix A).
// It is pure: Evaluate takes a snapshot of a donor's signals and returns the set
// of badge codes the donor currently qualifies for, so it is unit-testable
// without a database and the store layer only has to supply the snapshot and
// persist the permanent earn-state.
package badges

// Perk categories a badge may unlock (PRD B-3). Most badges have none.
type Perk string

const (
	PerkNone            Perk = ""
	PerkFoundingEvents  Perk = "FOUNDING_EVENTS"
	PerkMilestoneEvents Perk = "MILESTONE_EVENTS"
	PerkTeamCaptain     Perk = "TEAM_CAPTAIN"
)

// Badge is one catalog entry. Code is the stable identifier stored in the earn
// state and sent to clients; Rule is how it is earned, shown on an unearned
// badge (B-1). Live is false for a badge the engine does not award itself:
// Winning Team is awarded when a team month closes.
type Badge struct {
	Code  string
	Title string
	Rule  string
	Perk  Perk
	Live  bool
}

// Catalog is the 24 approved badges in Appendix A order. Codes are ASCII forms
// of the appendix's, which reuses "7" and uses symbols.
var Catalog = []Badge{
	{"F", "Founding Donor", "Start a monthly gift before January 2027, or join the website waitlist", PerkFoundingEvents, true},
	{"1D", "First Day", "Complete your first gift", PerkNone, true},
	{"7", "One Week", "Reach a 7-day streak", PerkNone, true},
	{"30", "One Month", "Reach a 30-day streak", PerkNone, true},
	{"100", "Hundred", "Reach a 100-day streak", PerkNone, true},
	{"Y1", "One Year", "Reach a 365-day streak", PerkNone, true},
	{"1K", "1,000 Days", "Reach a 1,000-day streak", PerkMilestoneEvents, true},
	{"Y3", "Three Years", "Reach a 1,095-day streak", PerkNone, true},
	{"CB", "Comeback", "Restart giving after a lapse and reach 30 days", PerkNone, true},
	{"UP", "Level Up", "Raise your daily amount", PerkNone, true},
	{"REF1", "First Referral", "Someone you invited completes a gift", PerkNone, true},
	{"REF3", "Brought 3 In", "Three people you invited complete a gift", PerkNone, true},
	{"REF12", "Brought 12 In", "Twelve people you invited complete a gift", PerkNone, true},
	{"TC", "Team Captain", "Twenty people you invited complete a gift", PerkTeamCaptain, true},
	{"IRL", "IRL", "Someone joins by scanning your IRL code", PerkNone, true},
	{"V", "Volunteer", "Opt in as a volunteer", PerkNone, true},
	{"SU", "Showed Up", "Check in at your first event", PerkNone, true},
	{"V3", "Volunteer ×3", "Check in at 3 events", PerkNone, true},
	{"V10", "Volunteer ×10", "Check in at 10 events", PerkNone, true},
	{"INIT7", "7 Initiatives", "Fund 7 different initiatives", PerkNone, true},
	{"CITY50", "Top 50 City", "Rank in the top 50 in your city", PerkNone, true},
	{"NAT2", "Top 2%", "Rank in the top 2% nationally by streak", PerkNone, true},
	{"TP", "Team Player", "Join a company team", PerkNone, true},
	{"WT", "Winning Team", "Your team finishes a month at #1 on your company leaderboard", PerkNone, false},
}

// byCode indexes the catalog for O(1) lookup.
var byCode = func() map[string]Badge {
	m := make(map[string]Badge, len(Catalog))
	for _, b := range Catalog {
		m[b.Code] = b
	}
	return m
}()

// Lookup returns the catalog badge for a code.
func Lookup(code string) (Badge, bool) { b, ok := byCode[code]; return b, ok }

// CodesForPerk returns the badge codes that carry a given perk. Used by the
// invite-only event gate (E-5): a donor "holds" the perk when they hold any of
// these badges. Returns nil for PerkNone or an unknown perk.
func CodesForPerk(perk Perk) []string {
	if perk == PerkNone {
		return nil
	}
	var codes []string
	for _, b := range Catalog {
		if b.Perk == perk {
			codes = append(codes, b.Code)
		}
	}
	return codes
}

// HoldsPerk reports whether any of the donor's earned badge codes carries perk.
func HoldsPerk(earned map[string]bool, perk Perk) bool {
	if perk == PerkNone {
		return false
	}
	for _, code := range CodesForPerk(perk) {
		if earned[code] {
			return true
		}
	}
	return false
}

// Signals is the donor's current earning state, assembled by the store.
type Signals struct {
	FirstGiftMade       bool   // completed a first gift
	FoundingQualified   bool   // first gift before the Founding cutoff / waitlist
	StreakDays          int64  // days since the current run began
	ComebackReached30   bool   // restarted after a lapse and reached a 30-day run
	RaisedAmount        bool   // increased their monthly amount at least once
	ConvertedReferrals  int64  // referrals who completed a gift
	HasIRLReferral      bool   // someone joined by scanning their IRL code
	DistinctInitiatives int64  // distinct initiatives funded/shown (A-4)
	NationalTopPercent  *int   // national percentile rank (1 = top 1%); nil if unranked
	NationalPopulation  int    // donors ranked nationally alongside them
	CityRank            *int   // rank within their city; nil if unranked
	City                string // the city CityRank is within; empty if unranked
	CityPopulation      int    // donors ranked in City alongside them
	VolunteerOptIn      bool   // opted in as a volunteer (E-1)
	EventCheckins       int64  // events the donor has checked in to (E-8)
	JoinedTeam          bool   // on a company team (EM-4)
}

// MinRankPopulation is the fewest ranked donors a rank badge is awarded among
// by default: a rank among a handful of donors is not an achievement.
const MinRankPopulation = 100

// streakDayBadges maps the streak-day badges to their thresholds.
var streakDayBadges = []struct {
	code string
	days int64
}{{"7", 7}, {"30", 30}, {"100", 100}, {"Y1", 365}, {"1K", 1000}, {"Y3", 1095}}

// Evaluate returns the Live badges the donor currently qualifies for. The
// store keeps every badge ever earned, so one that no longer qualifies stays
// earned (B-2).
func Evaluate(s Signals) map[string]bool {
	earned := map[string]bool{}
	if s.FoundingQualified {
		earned["F"] = true
	}
	if s.FirstGiftMade {
		earned["1D"] = true
	}
	for _, m := range streakDayBadges {
		if s.StreakDays >= m.days {
			earned[m.code] = true
		}
	}
	if s.ComebackReached30 {
		earned["CB"] = true
	}
	if s.RaisedAmount {
		earned["UP"] = true
	}
	for _, r := range []struct {
		code string
		n    int64
	}{{"REF1", 1}, {"REF3", 3}, {"REF12", 12}, {"TC", 20}} {
		if s.ConvertedReferrals >= r.n {
			earned[r.code] = true
		}
	}
	if s.HasIRLReferral {
		earned["IRL"] = true
	}
	if s.DistinctInitiatives >= 7 {
		earned["INIT7"] = true
	}
	if s.NationalTopPercent != nil && *s.NationalTopPercent <= 2 && s.NationalPopulation >= MinRankPopulation {
		earned["NAT2"] = true
	}
	if s.CityRank != nil && *s.CityRank <= 50 && s.CityPopulation >= MinRankPopulation {
		earned["CITY50"] = true
	}
	if s.VolunteerOptIn {
		earned["V"] = true
	}
	for _, r := range []struct {
		code string
		n    int64
	}{{"SU", 1}, {"V3", 3}, {"V10", 10}} {
		if s.EventCheckins >= r.n {
			earned[r.code] = true
		}
	}
	if s.JoinedTeam {
		earned["TP"] = true
	}
	for code := range earned {
		if b, ok := byCode[code]; !ok || !b.Live {
			delete(earned, code)
		}
	}
	return earned
}
