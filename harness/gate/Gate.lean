-- Emitted from wingline.kernel Bounds and NEXT. Do not edit.
-- nu = 1.5e-05
-- delta_cl = 0.05
-- delta_cd = 0.01
-- screening: This mesh is not a design answer until the refinement gate passes.
-- design: Design grade is force agreement between the parent mesh and a 20% chordwise refinement on this wall-modeled Spalart-Allmaras setup. It is not mesh independence and it is not a flight-hardware release.

inductive Shape where
  | empty
  | pilot_red
  | pilot_green
  | scoped
  | mesh_open
  | mesh_green
  | mesh_closed
  | solve_open
  | solve_green
  | solve_closed
  | forces_green
  | forces_red
  deriving DecidableEq, Repr

inductive Stage where
  | pilot | scope | mesh | solve | forces
  deriving DecidableEq, Repr

inductive Status where
  | green | red | stopped
  deriving DecidableEq, Repr

inductive Review where
  | affirm | challenge
  deriving DecidableEq, Repr

inductive Refine where
  | absent | green | red | stopped
  deriving DecidableEq, Repr

inductive Label where
  | screening | design | refusal
  deriving DecidableEq, Repr

def deltaClBound : Nat := 50
def deltaCdBound : Nat := 10

def deltaInside (dcl dcd : Int) : Bool :=
  decide (Int.natAbs dcl ≤ deltaClBound ∧ Int.natAbs dcd ≤ deltaCdBound)

def nextOk : Shape → Stage → Status → Bool
  | .empty, .pilot, .green => true
  | .empty, .pilot, .red => true
  | .pilot_green, .scope, .green => true
  | .scoped, .mesh, .green => true
  | .scoped, .mesh, .red => true
  | .mesh_open, .mesh, .green => true
  | .mesh_open, .mesh, .red => true
  | .mesh_open, .mesh, .stopped => true
  | .mesh_green, .solve, .green => true
  | .mesh_green, .solve, .red => true
  | .solve_open, .solve, .green => true
  | .solve_open, .solve, .red => true
  | .solve_open, .solve, .stopped => true
  | .solve_green, .forces, .green => true
  | .solve_green, .forces, .red => true
  | _, _, _ => false

def step (s : Shape) (g : Stage) (st : Status) : Shape :=
  if nextOk s g st then
    match s, g, st with
    | .empty, .pilot, .green => .pilot_green
    | .empty, .pilot, .red => .pilot_red
    | .pilot_green, .scope, .green => .scoped
    | .scoped, .mesh, .green => .mesh_green
    | .scoped, .mesh, .red => .mesh_open
    | .mesh_open, .mesh, .green => .mesh_green
    | .mesh_open, .mesh, .red => .mesh_open
    | .mesh_open, .mesh, .stopped => .mesh_closed
    | .mesh_green, .solve, .green => .solve_green
    | .mesh_green, .solve, .red => .solve_open
    | .solve_open, .solve, .green => .solve_green
    | .solve_open, .solve, .red => .solve_open
    | .solve_open, .solve, .stopped => .solve_closed
    | .solve_green, .forces, .green => .forces_green
    | .solve_green, .forces, .red => .forces_red
    | _, _, _ => s
  else
    s

def taintedOf : Status → Bool
  | .green => false
  | .red => true
  | .stopped => true

/-- Report label. Design is one row: untainted forces-green, physics green,
    review affirm, refinement green, and delta inside the emitted bound. -/
def label (shape : Shape) (tainted physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool) : Label :=
  match tainted, shape, physicsGreen, review, refine, inside with
  | false, .forces_green, true, .affirm, .green, true => .design
  | false, .forces_green, true, _, _, _ => .screening
  | _, _, _, _, _, _ => .refusal

theorem delta_inside_bound_endpoints :
    deltaInside 50 10 = true ∧
    deltaInside (50 + 1) 0 = false ∧
    deltaInside (-50) (-10) = true := by
  decide

theorem report_unreachable_after_failure
    (shape : Shape) (prior : Status) (physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool)
    (h : prior = .red ∨ prior = .stopped) :
    label shape (taintedOf prior) physicsGreen review refine inside ≠ .screening ∧
    label shape (taintedOf prior) physicsGreen review refine inside ≠ .design := by
  cases h <;> cases prior <;> cases shape <;> cases physicsGreen <;> cases review <;>
    cases refine <;> cases inside <;> simp [label, taintedOf] at *

theorem design_needs_refinement
    (shape : Shape) (tainted physicsGreen : Bool) (review : Review)
    (refine : Refine) (inside : Bool)
    (h : label shape tainted physicsGreen review refine inside = .design) :
    refine = .green ∧ inside = true ∧ review = .affirm := by
  cases tainted <;> cases shape <;> cases physicsGreen <;> cases review <;>
    cases refine <;> cases inside <;> simp [label] at h
  all_goals try cases h
  exact ⟨rfl, rfl, rfl⟩

theorem review_cannot_mint_design
    (shape : Shape) (tainted physicsGreen : Bool) (refine : Refine) (inside : Bool) :
    label shape tainted physicsGreen .challenge refine inside ≠ .design := by
  cases shape <;> cases tainted <;> cases physicsGreen <;> cases refine <;> cases inside <;>
    simp [label]
  all_goals (intro contra; cases contra)

theorem bad_step_does_not_enter_report
    (s : Shape) (g : Stage) (st : Status)
    (hbad : st = .red ∨ st = .stopped) (hfrom : s ≠ .forces_green) :
    step s g st ≠ .forces_green := by
  cases hbad <;> cases s <;> cases g <;> cases st <;> simp [step, nextOk] at *
