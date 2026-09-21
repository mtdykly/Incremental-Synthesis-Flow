module top(input [3:0] a,b,c, input sel, output logic [3:0] y, output z);
  always_comb begin
    y=a;
    if(sel) y=b;
  end
  assign z=^c;
endmodule
