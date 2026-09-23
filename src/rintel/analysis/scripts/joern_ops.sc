// FLOW1-ANALYZER0 / ANALYZER-MAP0 fixed Joern operations (sidecar).
// Frozen: only ops in {symbols, calls, cfg, dataflow, slice} (plus the
// batch wrapper) — arbitrary CPGQL is never possible through this channel.
// Invocation:
//   joern --script joern_ops.sc --param input=/abs/repo --param op=calls
//         [--param symbol=q] [--param language=python|c]
//         [--param ops=symbols,calls,cfg] [--param project=NAME]
// Phase 7 modes:
//   * default: importCode once, then run the ops in THIS invocation
//     (batch mode: op=batch + ops=..., one JVM, single index).
//   * project=NAME: open the saved workspace project instead of importing
//     (persistent-project reuse: index once, query many invocations).
import io.joern.dataflowengineoss.language._
import io.joern.dataflowengineoss.queryengine.EngineContext
import io.shiftleft.semanticcpg.language._
import ujson._
import scala.collection.mutable.ListBuffer

def toJson(v: Any): ujson.Value = v match {
  case m: Map[_, _]  => ujson.Obj.from(m.map { case (k, vv) =>
      k.toString -> toJson(vv) })
  case l: List[_]    => ujson.Arr.from(l.map(toJson))
  case s: String     => ujson.Str(s)
  case i: Int        => ujson.Num(i.toDouble)
  case l: Long       => ujson.Num(l.toDouble)
  case l: Float      => ujson.Num(l.toDouble)
  case d: Double     => ujson.Num(d)
  case b: Boolean    => ujson.Bool(b)
  case o: Option[_]  => toJson(o.getOrElse(null))
  case null          => ujson.Null
  case other         => ujson.Str(other.toString)
}

@main def run(input: String, op: String, symbol: String = "",
             language: String = "python", project: String = "",
             ops: String = ""): Unit = {
  if (project.isEmpty) {
    val _ = language match {
      case "c"   => importCode.c(input)
      case _     => importCode.python(input)
    }
  } else {
    open(project)
  }

  def loc(m: Any): Map[String, Any] =
    try {
      val fn = m.asInstanceOf[io.shiftleft.codepropertygraph.generated.nodes.Method]
      Map("name" -> fn.name, "fullName" -> fn.fullName,
          "filename" -> fn.filename, "lineNumber" -> fn.lineNumber)
    } catch { case _: Throwable => Map.empty }

  def callLoc(c: Any): Map[String, Any] =
    try {
      val cn = c.asInstanceOf[io.shiftleft.codepropertygraph.generated.nodes.Call]
      Map("name" -> cn.name, "methodFullName" -> cn.methodFullName,
          "caller" -> cn.method.fullName,
          "lineNumber" -> cn.lineNumber, "order" -> cn.order)
    } catch { case _: Throwable => Map.empty }

  val engine = new EngineContext()
  val opList = if (op == "batch")
    ops.split(",").filter(_.nonEmpty).toList else List(op)

  for (single <- opList) {
    val out: Map[String, Any] = single match {
      case "symbols" =>
        val ms = cpg.method
          .filterNot(n => n.name.contains("<"))
          .map(loc).toList
        Map("op" -> "symbols", "count" -> ms.size, "items" -> ms)

      case "calls" =>
        val cs = cpg.call
          .filterNot(cn => cn.name.contains("<"))
          .filterNot(cn => cn.name == "import")
          .filterNot(cn => cn.name == "importFrom")
          .map(callLoc).toList
        val resolved = cs.count(c => c.getOrElse("methodFullName", "")
          .toString != "<unknownFullName>")
        Map("op" -> "calls", "count" -> cs.size,
            "resolved" -> resolved, "items" -> cs)

      case "cfg" =>
        val target = if (symbol.isEmpty) cpg.method
                     else cpg.method.fullNameExact(symbol)
        if (target.size == 0)
          Map("op" -> "cfg", "count" -> 0, "items" -> List.empty)
        else {
          val edges = for {
            m <- target.l
            e <- m.cfgNode.l.sliding(2).collect { case Seq(a, b) => (a, b) }
          } yield Map("method" -> m.fullName, "fromId" -> e._1.id,
                      "fromCode" -> e._1.code, "toId" -> e._2.id,
                      "toCode" -> e._2.code)
          val ctrls = cpg.controlStructure
            .map(cs => Map("name" -> cs.controlStructureType.toString,
                           "code" -> cs.code,
                           "line" -> cs.lineNumber.getOrElse(-1)))
            .toList
          Map("op" -> "cfg", "count" -> edges.size,
              "edges" -> edges, "controls" -> ctrls)
        }

      case "dataflow" =>
        val target = if (symbol.isEmpty) cpg.method
                     else cpg.method.fullNameExact(symbol)
        if (target.size == 0)
          Map("op" -> "dataflow", "count" -> 0, "items" -> List.empty)
        else {
          var total = 0
          val flows = ListBuffer.empty[Map[String, Any]]
          for (m <- target.l.take(20)) {
            val sources = m.parameter.l
            val sinks = m.call.l
            val res = sinks.reachableByFlows(sources)(engine)
            total += res.size
            res.take(40).foreach { f =>
              val elems = f.elements.l.map(e =>
                Map("node" -> e.node.label, "code" -> e.code,
                    "line" -> e.lineNumber.getOrElse(-1)))
              flows += Map("sinkMethod" -> m.fullName,
                           "elements" -> elems)
            }
          }
          Map("op" -> "dataflow", "count" -> total, "items" -> flows.toList)
        }

      case "slice" =>
        val target = cpg.method.fullNameExact(symbol)
        if (target.size == 0)
          Map("op" -> "slice", "count" -> 0, "items" -> List.empty)
        else {
          val m = target.head
          val sources = m.parameter.l
          val res = m.call.l.reachableByFlows(sources)(engine)
          val nodes = ListBuffer.empty[Map[String, Any]]
          res.take(40).foreach { f =>
            f.elements.l.foreach { e =>
              nodes += Map("label" -> e.node.label, "code" -> e.code,
                           "line" -> e.lineNumber.getOrElse(-1))
            }
          }
          Map("op" -> "slice", "count" -> nodes.size, "items" -> nodes.toList)
        }

      case other =>
        Map("op" -> other, "error" -> s"unknown operation '$other'")
    }
    println(ujson.write(toJson(out)))
  }
}
