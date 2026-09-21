// Pre-proc, whole-process ECO extraction. No RTLIL text rewriting.
#include "kernel/yosys.h"
#include "kernel/sigtools.h"
#include "kernel/celltypes.h"
#include "libs/json11/json11.hpp"
#include <fstream>
#include <functional>
#include <sstream>

USING_YOSYS_NAMESPACE
PRIVATE_NAMESPACE_BEGIN
using J = json11::Json;

std::string name(IdString id) { return RTLIL::unescape_id(id); }
std::string src(RTLIL::AttrObject *obj) {
    return obj->attributes.count(ID::src) ? obj->attributes.at(ID::src).decode_string() : "";
}
void save(const std::string &path, J data) {
    std::ofstream out(path);
    if (!out) log_cmd_error("Cannot write %s.\n", path.c_str());
    out << data.dump() << '\n';
}
J read_request(const std::string &path) {
    std::ifstream in(path);
    if (!in) log_cmd_error("Cannot read request %s.\n", path.c_str());
    std::stringstream buf; buf << in.rdbuf();
    std::string err; J j = J::parse(buf.str(), err);
    if (!err.empty() || !j.is_object()) log_cmd_error("Invalid request JSON: %s.\n", err.c_str());
    return j;
}
void case_signals(RTLIL::CaseRule *c, SigSpec &reads, SigSpec &writes) {
    for (auto &x : c->compare) reads.append(x);
    for (auto &a : c->actions) { writes.append(a.first); reads.append(a.second); }
    for (auto sw : c->switches) {
        reads.append(sw->signal);
        for (auto child : sw->cases) case_signals(child, reads, writes);
    }
}
bool process_signals(RTLIL::Process *p, SigSpec &reads, SigSpec &writes) {
    bool comb = true;
    case_signals(&p->root_case, reads, writes);
    for (auto sync : p->syncs) {
        comb &= sync->type == RTLIL::STa && sync->mem_write_actions.empty();
        reads.append(sync->signal);
        for (auto &a : sync->actions) { writes.append(a.first); reads.append(a.second); }
    }
    return comb;
}
std::string process_src(RTLIL::Process *p) {
    std::string result = src(p);
    auto add = [&](RTLIL::AttrObject *obj) {
        auto value = src(obj);
        if (!value.empty()) result += "|" + value;
    };
    std::function<void(RTLIL::CaseRule*)> visit = [&](RTLIL::CaseRule *c) {
        add(c);
        for (auto sw : c->switches) {
            add(sw);
            for (auto child : sw->cases) visit(child);
        }
    };
    visit(&p->root_case);
    SigSpec reads, writes; process_signals(p, reads, writes);
    pool<Wire*> seen;
    for (auto b : reads) if (b.wire && !seen.count(b.wire)) {
        seen.insert(b.wire); add(b.wire);
    }
    return result;
}
J aliases(Module *m, SigMap &map, const SigSpec &sig) {
    pool<SigBit> bits; for (auto b : map(sig)) bits.insert(b);
    J::array result;
    for (auto w : m->wires()) {
        if (!w->name.isPublic()) continue;
        for (int i = 0; i < w->width; i++)
            if (bits.count(map(SigBit(w, i))))
                result.push_back(J::object{{"signal", name(w->name)}, {"offset", i}});
    }
    return result;
}
J wire_info(Wire *w) {
    return J::object{{"signal", name(w->name)}, {"width", w->width},
        {"signed", w->is_signed}, {"start_offset", w->start_offset}, {"upto", w->upto},
        {"input", w->port_input}, {"output", w->port_output}, {"src", src(w)}};
}
// Structural semantic snapshots use RTLIL objects, not source text. Public
// symbols are stable leaves; anonymous expression cells are expanded, and
// process temporaries get traversal-local IDs (independent of Yosys autoidx).
struct Semantic {
    SigMap map;
    CellTypes types;
    dict<SigBit, J> public_bits;
    dict<SigBit, std::pair<Cell*, std::pair<IdString, int>>> drivers;
    dict<SigBit, int> temporaries;
    pool<SigBit> local, active;
    Semantic(Design *design, Module *m) : map(m), types(design) {
        std::vector<Wire*> wires;
        for (auto w : m->wires()) if (w->name.isPublic()) wires.push_back(w);
        std::sort(wires.begin(), wires.end(), [](Wire *a, Wire *b) {
            return std::make_pair(!a->port_input, a->name.str()) < std::make_pair(!b->port_input, b->name.str());
        });
        for (auto w : wires) for (int i = 0; i < w->width; i++) {
            auto bit = map(SigBit(w, i));
            if (bit.wire && !public_bits.count(bit))
                public_bits[bit] = J::object{{"wire", name(w->name)}, {"offset", i}};
        }
        for (auto c : m->cells()) for (auto &p : c->connections()) if (types.cell_output(c->type, p.first))
            for (int i = 0; i < GetSize(p.second); i++)
                drivers[map(p.second[i])] = {c, {p.first, i}};
    }
    J attrs(RTLIL::AttrObject *obj) {
        J::object result;
        for (auto &a : obj->attributes)
            if (a.first != ID::src && a.first != ID::scopename && a.first != ID::hdlname)
                result[name(a.first)] = a.second.as_string();
        return result;
    }
    J bit(SigBit b) {
        b = map(b);
        if (!b.wire) return J::object{{"constant", log_signal(b)}};
        if (public_bits.count(b)) return public_bits.at(b);
        if (!local.count(b) && drivers.count(b) && !active.count(b)) {
            active.insert(b);
            auto driver = drivers.at(b);
            J result = J::object{{"expression", cell(driver.first, false)},
                {"pin", name(driver.second.first)}, {"offset", driver.second.second}};
            active.erase(b);
            return result;
        }
        if (!temporaries.count(b)) temporaries[b] = GetSize(temporaries);
        return J::object{{"temporary", temporaries.at(b)}};
    }
    J sig(SigSpec signal) {
        J::array result; for (auto b : signal) result.push_back(bit(b)); return result;
    }
    J actions(const std::vector<RTLIL::SigSig> &items) {
        J::array result;
        for (auto &a : items) result.push_back(J::array{sig(a.first), sig(a.second)});
        return result;
    }
    J case_rule(RTLIL::CaseRule *c) {
        J::array compare, switches;
        for (auto &x : c->compare) compare.push_back(sig(x));
        // Keep the ordering of all actions, switch rules and cases.
        J assigned = actions(c->actions);
        for (auto sw : c->switches) {
            J signal = sig(sw->signal); J::array cases;
            for (auto child : sw->cases) cases.push_back(case_rule(child));
            switches.push_back(J::object{{"signal", signal}, {"cases", cases}, {"attributes", attrs(sw)}});
        }
        return J::object{{"compare", compare}, {"actions", assigned}, {"switches", switches}, {"attributes", attrs(c)}};
    }
    J process(RTLIL::Process *p) {
        SigSpec reads, writes; process_signals(p, reads, writes);
        for (auto b : map(writes)) local.insert(b);
        J root = case_rule(&p->root_case); J::array syncs;
        for (auto sync : p->syncs)
            syncs.push_back(J::object{{"type", int(sync->type)}, {"signal", sig(sync->signal)},
                {"actions", actions(sync->actions)}, {"memory_writes", int(sync->mem_write_actions.size())}});
        return J::object{{"root", root}, {"syncs", syncs}, {"attributes", attrs(p)}};
    }
    J cell(Cell *c, bool include_outputs = true) {
        J::object params, connections;
        for (auto &p : c->parameters) params[name(p.first)] = p.second.as_string();
        // Sort port names before assigning traversal-local temporary IDs.
        std::map<std::string, SigSpec> ports;
        for (auto &p : c->connections())
            if (include_outputs || types.cell_input(c->type, p.first)) ports[name(p.first)] = p.second;
        if (include_outputs) for (auto &p : c->connections())
            if (types.cell_output(c->type, p.first)) for (auto b : map(p.second)) local.insert(b);
        for (auto &p : ports) connections[p.first] = sig(p.second);
        return J::object{{"type", name(c->type)}, {"parameters", params},
            {"connections", connections}, {"attributes", attrs(c)}};
    }
};
void index_instance(Design *design, Module *m, J::array path, J::array &instances, pool<IdString> stack) {
    if (stack.count(m->name)) log_cmd_error("Recursive hierarchy is unsupported.\n");
    stack.insert(m->name);
    SigMap map(m); CellTypes types(design);
    J::array wires, objects;
    for (auto w : m->wires()) if (w->name.isPublic()) wires.push_back(wire_info(w));
    for (auto &entry : m->processes) {
        SigSpec reads, writes; bool comb = process_signals(entry.second, reads, writes);
        objects.push_back(J::object{{"kind", "process"}, {"name", name(entry.first)},
            {"src", process_src(entry.second)}, {"combinational", comb}, {"outputs", aliases(m, map, writes)}, {"semantic", Semantic(design, m).process(entry.second)}});
    }
    for (auto c : m->cells()) {
        SigSpec outputs;
        for (auto &p : c->connections()) if (types.cell_output(c->type, p.first)) outputs.append(p.second);
        objects.push_back(J::object{{"kind", design->module(c->type) ? "instance" : "cell"},
            {"name", name(c->name)}, {"type", name(c->type)}, {"src", src(c)},
            {"outputs", aliases(m, map, outputs)}, {"semantic", Semantic(design, m).cell(c)}});
    }
    // Connections are aliases/continuous assignments already elaborated by the frontend.
    for (auto &conn : m->connections())
        objects.push_back(J::object{{"kind", "connection"}, {"outputs", aliases(m, map, conn.first)},
            {"semantic", Semantic(design, m).sig(conn.second)}});
    instances.push_back(J::object{{"path", path}, {"module", name(m->name)}, {"src", src(m)},
        {"wires", wires}, {"objects", objects}, {"memories", int(m->memories.size())}});
    for (auto c : m->cells()) if (auto child = design->module(c->type)) {
        auto child_path = path; child_path.push_back(name(c->name));
        index_instance(design, child, child_path, instances, stack);
    }
}
struct EcoIndex : Pass {
    EcoIndex() : Pass("eco_index", "index hierarchical pre-proc ECO objects") {}
    void execute(std::vector<std::string> args, Design *design) override {
        std::string top, file; size_t i = 1;
        for (; i + 1 < args.size(); i += 2) {
            if (args[i] == "-top") top = args[i+1];
            else if (args[i] == "-json") file = args[i+1];
            else break;
        }
        extra_args(args, i, design);
        rewrite_filename(file);
        auto m = design->module(RTLIL::escape_id(top));
        if (!m || file.empty()) log_cmd_error("eco_index requires -top and -json.\n");
        J::array instances; index_instance(design, m, {}, instances, {});
        save(file, J::object{{"schema_version", 1}, {"stage", "hierarchy_before_proc"},
            {"top", top}, {"instances", instances}});
    }
} EcoIndex;

void require_comb(Design *design, Module *m, pool<IdString> &visited) {
    if (visited.count(m->name)) return;
    visited.insert(m->name);
    if (!m->memories.empty()) log_cmd_error("ECO region contains memory in %s.\n", log_id(m));
    CellTypes types(design);
    for (auto &p : m->processes) {
        SigSpec reads, writes;
        if (!process_signals(p.second, reads, writes))
            log_cmd_error("ECO region contains non-combinational process %s.\n", log_id(p.first));
    }
    for (auto c : m->cells()) {
        if (auto child = design->module(c->type)) require_comb(design, child, visited);
        else if (!types.cell_known(c->type) || c->is_builtin_ff() || c->is_mem_cell() ||
                 c->type.str().find("$assert") == 0 || c->type.str().find("$assume") == 0 ||
                 c->type.str().find("$any") == 0 || c->type.str().find("$all") == 0 ||
                 c->type.str().find("$print") == 0 || c->type.str().find("$check") == 0)
            log_cmd_error("Unsupported ECO cell %s (%s).\n", log_id(c), log_id(c->type));
    }
}
struct EcoExtract : Pass {
    EcoExtract() : Pass("eco_extract", "extract atomic combinational processes before proc") {}
    void help() override {
        log("    eco_extract -spec request.json -report extraction.json\n\n");
        log("Copy the backward dependency closure of requested output bits, stopping\n");
        log("only at declared input cuts. Whole processes and instances are atomic.\n");
    }
    void execute(std::vector<std::string> args, Design *design) override {
        std::string file, report; size_t i = 1;
        for (; i + 1 < args.size(); i += 2) {
            if (args[i] == "-spec") file = args[i+1];
            else if (args[i] == "-report") report = args[i+1];
            else break;
        }
        extra_args(args, i, design);
        rewrite_filename(file); rewrite_filename(report);
        if (file.empty() || report.empty()) log_cmd_error("eco_extract requires -spec and -report.\n");
        J spec = read_request(file);
        auto source = design->module(RTLIL::escape_id(spec["top"].string_value()));
        if (!source || !spec["instance_path"].is_array()) log_cmd_error("Invalid top or instance_path.\n");
        for (auto &part : spec["instance_path"].array_items()) {
            auto c = source->cell(RTLIL::escape_id(part.string_value()));
            source = c ? design->module(c->type) : nullptr;
            if (!source) log_cmd_error("ECO instance path does not exist.\n");
        }
        if (design->module(ID(incremental_region))) log_cmd_error("Reserved module incremental_region exists.\n");
        auto m = design->addModule(ID(incremental_region)); source->cloneInto(m);
        m->attributes.erase(ID::top); m->attributes[ID::top] = 1;
        SigMap map(m); CellTypes types(design);
        pool<SigBit> cuts;
        std::vector<std::pair<IdString, SigBit>> inputs, outputs;
        pool<IdString> port_names;
        for (std::string dir : {"inputs", "outputs"}) {
            if (!spec[dir].is_array()) log_cmd_error("Missing %s.\n", dir.c_str());
            for (auto &item : spec[dir].array_items()) {
                auto w = m->wire(RTLIL::escape_id(item["signal"].string_value()));
                int offset = item["offset"].int_value();
                if (!w || !item["offset"].is_number() || offset < 0 || offset >= w->width ||
                    w->width != item["width"].int_value() || w->is_signed != item["signed"].bool_value() ||
                    w->start_offset != item["start_offset"].int_value() || w->upto != item["upto"].bool_value())
                    log_cmd_error("ECO boundary layout changed: %s.\n", item["signal"].string_value().c_str());
                auto port = RTLIL::escape_id(item["port"].string_value());
                if (name(port).empty() || port_names.count(port) || m->wire(port))
                    log_cmd_error("Duplicate/reserved ECO boundary port.\n");
                port_names.insert(port);
                auto bit = map(SigBit(w, offset));
                if (dir == "inputs") {
                    if (!bit.wire || cuts.count(bit)) log_cmd_error("Constant or aliased input cut.\n");
                    cuts.insert(bit); inputs.emplace_back(port, bit);
                } else outputs.emplace_back(port, bit);
            }
        }
        // Canonicalize actual in-memory signals, then discard redundant alias connections.
        auto rewrite = [&](SigSpec &sig) { sig = map(sig); };
        m->rewrite_sigspecs(rewrite); m->new_connections({});
        struct Unit { IdString id; bool process, comb; SigSpec reads, writes; };
        std::vector<Unit> units;
        dict<SigBit, pool<int>> drivers;
        for (auto &entry : m->processes) {
            Unit u; u.id = entry.first; u.process = true;
            u.comb = process_signals(entry.second, u.reads, u.writes); units.push_back(u);
        }
        for (auto c : m->cells()) {
            Unit u; u.id = c->name; u.process = false; u.comb = true;
            for (auto &p : c->connections()) {
                if (types.cell_input(c->type, p.first)) u.reads.append(p.second);
                if (types.cell_output(c->type, p.first)) u.writes.append(p.second);
            }
            units.push_back(u);
        }
        for (int j = 0; j < GetSize(units); j++)
            for (auto bit : units[j].writes) if (bit.wire) drivers[bit].insert(j);
        pool<int> selected;
        std::function<void(SigBit)> visit = [&](SigBit bit) {
            if (!bit.wire || cuts.count(bit)) return;
            if (!drivers.count(bit)) log_cmd_error("Uncertified dependency %s; expand the region.\n", log_signal(bit));
            if (GetSize(drivers.at(bit)) != 1) log_cmd_error("Multiple drivers at %s.\n", log_signal(bit));
            int j = *drivers.at(bit).begin();
            if (selected.count(j)) return;
            auto &u = units[j];
            if (!u.comb) log_cmd_error("ECO requires sequential process %s.\n", log_id(u.id));
            for (auto b : u.writes) if (cuts.count(b))
                log_cmd_error("Atomic process/instance writes an input cut; expand region.\n");
            selected.insert(j);
            for (auto b : u.reads) visit(b);
        };
        for (auto &out : outputs) visit(out.second);
        J::array processes, cells;
        for (int j = 0; j < GetSize(units); j++) {
            auto &u = units[j];
            if (selected.count(j)) {
                if (u.process) processes.push_back(name(u.id)); else cells.push_back(name(u.id));
            } else if (u.process) m->remove(m->processes.at(u.id)); else m->remove(m->cell(u.id));
        }
        // Unselected state/memory must not survive through attributes or declarations.
        for (auto w : m->wires()) {
            w->port_input = w->port_output = false; w->port_id = 0;
            w->attributes.erase(ID::init);
        }
        if (!m->memories.empty()) log_cmd_error("Memory declarations require a larger supported envelope.\n");
        m->ports.clear();
        for (auto &p : inputs) { auto w = m->addWire(p.first); w->port_input = true; m->connect(p.second, w); }
        for (auto &p : outputs) { auto w = m->addWire(p.first); w->port_output = true; m->connect(w, p.second); }
        m->fixup_ports();
        pool<IdString> checked; require_comb(design, m, checked);
        save(report, J::object{{"schema_version", 1}, {"status", "extracted"},
            {"instance_path", spec["instance_path"]}, {"source_module", name(source->name)},
            {"atomic_processes", processes}, {"atomic_cells", cells},
            {"inputs", spec["inputs"]}, {"outputs", spec["outputs"]}});
        log("ECO extracted %d whole processes and %d cells.\n", GetSize(processes), GetSize(cells));
    }
} EcoExtract;
PRIVATE_NAMESPACE_END
