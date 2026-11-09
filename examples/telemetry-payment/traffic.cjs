// Sends synthetic, non-charging requests to the unmodified upstream payment service.
const grpc = require('/usr/src/app/node_modules/@grpc/grpc-js');
const loader = require('/usr/src/app/node_modules/@grpc/proto-loader');
const definitions = loader.loadSync('/usr/src/app/demo.proto');
const proto = grpc.loadPackageDefinition(definitions).oteldemo;
const client = new proto.PaymentService('payment:50051', grpc.credentials.createInsecure());
let inFlight = false;
setInterval(() => {
  if (inFlight) return;
  inFlight = true;
  const metadata = new grpc.Metadata();
  metadata.set('baggage', 'synthetic_request=true');
  client.charge({
    amount: {currencyCode: 'USD', units: 1, nanos: 0},
    creditCard: {
      creditCardNumber: '4111111111111111', creditCardCvv: 123,
      creditCardExpirationYear: 2099, creditCardExpirationMonth: 12,
    },
  }, metadata, {deadline: Date.now() + 3000}, (error) => {
    console.log(error ? 'synthetic charge failed' : 'synthetic charge succeeded');
    inFlight = false;
  });
}, 1000);
