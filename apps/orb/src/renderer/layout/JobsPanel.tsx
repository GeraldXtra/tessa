/**
 * The reference's right-hand jobs list — seven rows with orange folder icons,
 * an UPLOAD and an INSERT button.
 *
 * ─── EVERY ROW IN THAT LIST IS INVENTED ───
 * "Daily Reddit Automation — Blocked", "Pulse 4:32 PM Pipeline — Aug 4",
 * "ScheduleBot: Queue today's two…". CONTRACT §4.1 defines `evt.job.created`,
 * `evt.job.progress`, `evt.job.updated` and `evt.job.completed`, and `core/`
 * emits none of them — there is no scheduler. The panel is built to that
 * contract's shape and shows NO DATA until something emits it.
 *
 * ─── THE ICONS, AND WHICH READING I TOOK ───
 * He ruled "no icons" when asked about the icon RAIL down the far left, and
 * that ruling is honoured: the rail is type, as §R.7 specifies. An icon in a
 * list row is a different object answering a different question — the rail's
 * icons replaced words a person has to learn, where a row icon sits beside the
 * word it decorates. I have still not built them, for a simpler reason: a
 * folder glyph beside a row would be the only iconography on the surface, and
 * one icon is a loose end rather than a system. The tier dot the approval card
 * already uses is the established idiom here and is what these rows will take.
 *
 * UPLOAD and INSERT are not built. Both would need a command that accepts a
 * file from the surface, and CONTRACT §5 has none — proposing one is a separate
 * conversation about a genuinely new capability, not part of a layout rebuild.
 */

export function JobsPanel() {
  return (
    <section className="panel-sec" aria-label="jobs">
      <h3 className="panel-sec__title">jobs</h3>
      <p className="panel-sec__nodata">NO DATA</p>
      <p className="panel-sec__why">
        no scheduler yet — fills from <code>evt.job.*</code>
      </p>
    </section>
  );
}
