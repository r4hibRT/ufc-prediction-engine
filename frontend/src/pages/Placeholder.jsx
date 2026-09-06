export default function Placeholder({ title, note }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      <p>{note}</p>
    </div>
  );
}
