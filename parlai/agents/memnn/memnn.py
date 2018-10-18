#!/usr/bin/env python3

# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree. An additional grant
# of patent rights can be found in the PATENTS file in the same directory.

from functools import lru_cache

import torch

from parlai.core.torch_ranker_agent import TorchRankerAgent

from .modules import MemNN, opt_to_kwargs


class MemnnAgent(TorchRankerAgent):
    """Memory Network agent.

    Tips:
    - time features are necessary when memory order matters
    - multiple hops allow multiple steps of reasoning, but also seem to make it
        easier to learn to read the memories if you have at least two hops
    - 'adam' seems to work very poorly compared to 'sgd' for hogwild training
    """

    @staticmethod
    def add_cmdline_args(argparser):
        arg_group = argparser.add_argument_group('MemNN Arguments')
        arg_group.add_argument(
            '--init-model', type=str, default=None,
            help='load dict/model/opts from this path')
        arg_group.add_argument(
            '-esz', '--embedding-size', type=int, default=128,
            help='size of token embeddings')
        arg_group.add_argument(
            '-hops', '--hops', type=int, default=3,
            help='number of memory hops')
        arg_group.add_argument(
            '--memsize', type=int, default=32,
            help='size of memory, set to 0 for "nomemnn" model which just '
                 'embeds query and candidates and picks most similar candidate')
        arg_group.add_argument(
            '-tf', '--time-features', type='bool', default=True,
            help='use time features for memory embeddings')
        arg_group.add_argument(
            '-pe', '--position-encoding', type='bool', default=False,
            help='use position encoding instead of bag of words embedding')
        TorchRankerAgent.add_cmdline_args(argparser)
        MemnnAgent.dictionary_class().add_cmdline_args(argparser)
        return arg_group

    @staticmethod
    def model_version():
        """Return current version of this model, counting up from 0.

        Models may not be backwards-compatible with older versions.
        Version 1 split from version 0 on Sep 7, 2018.
        To use version 0, use --model legacy:memnn:0
        (legacy agent code is located in parlai/agents/legacy_agents).
        """
        # TODO: Update date that Version 2 split and move version 1 to legacy
        return 2

    def __init__(self, opt, shared=None):
        # all instances may need some params
        super().__init__(opt, shared)

        self.id = 'MemNN'
        self.memsize = opt['memsize']
        if self.memsize < 0:
            self.memsize = 0
        self.use_time_features = opt['time_features']

        if not shared:
            if opt['time_features']:
                for i in range(self.memsize):
                    self.dict[self._time_feature(i)] = 100000000 + i

    def build_model(self):
        """Build MemNN model."""
        kwargs = opt_to_kwargs(self.opt)
        self.model = MemNN(len(self.dict), self.opt['embedding_size'],
                           padding_idx=self.NULL_IDX, **kwargs)

    def score_candidates(self, batch, cand_vecs):
        mems = self._build_mems(batch.memory_vecs)
        scores = self.model(batch.text_vec, mems, cand_vecs)
        return scores

    @lru_cache(maxsize=None)  # bounded by opt['memsize'], cache string concats
    def _time_feature(self, i):
        """Return time feature token at specified index."""
        return '__tf{}__'.format(i)

    def get_dialog_history(self, *args, **kwargs):
        """Override options in get_dialog_history from parent."""
        kwargs['add_p1_after_newln'] = True  # will only happen if -pt True
        return super().get_dialog_history(*args, **kwargs)

    def vectorize(self, *args, **kwargs):
        """Override options in vectorize from parent."""
        kwargs['add_start'] = False
        kwargs['add_end'] = False
        kwargs['split_lines'] = True
        return super().vectorize(*args, **kwargs)

    # def _build_train_cands(self, labels, label_cands=None):
    #     """Build candidates from batch labels.
    #
    #     When the batchsize is 1, first we look for label_cands to be filled
    #     (from batch.candidate_vecs). If available, we'll use those candidates.
    #     Otherwise, we'll rank each token in the dictionary except NULL.
    #
    #     For batches of labels of a single token, we use torch.unique to return
    #     only the unique tokens.
    #     For batches of label sequences of length greater than one, we keep them
    #     all so as not to waste too much time calculating uniqueness.
    #
    #     :param labels:      (bsz x seqlen) LongTensor.
    #     :param label_cands: default None. if bsz is 1 and label_cands is not
    #                         None, will use label_cands for training.
    #
    #     :return: tuple of tensors (cands, indices)
    #         cands is (num_cands <= bsz x seqlen) candidates
    #         indices is (bsz) index in cands of each original label
    #     """
    #     assert labels.dim() == 2
    #     if labels.size(0) == 1:
    #         # we can't rank the batch of labels, see if there are label_cands
    #         label = labels[0]  # there's just one
    #         if label_cands is not None:
    #             self._warn_once(
    #                 'ranking_labelcands',
    #                 '[ Training using label_candidates fields as cands. ]')
    #             label_cands, _ = padded_tensor(label_cands[0],
    #                                            use_cuda=self.use_cuda)
    #
    #             if label_cands.size(1) == 1:
    #                 # use unique if cands are 1D
    #                 label_cands = label_cands.unique(return_inverse=False)
    #                 label_cands.unsqueeze_(1)
    #             label_inds = (label_cands == label).all(1).nonzero()
    #             if label_inds.size(0) > 1:
    #                 label_inds = self.random.choice(label_inds)
    #             else:
    #                 label_inds.squeeze_(1)
    #             return label_cands, label_inds
    #         else:
    #             self._warn_once(
    #                 'ranking_dict',
    #                 '[ Training using dictionary of tokens as cands. ]')
    #             dict_size = len(self.dict)
    #             full_dict = labels.new(range(1, dict_size))
    #             # pick random token from label
    #             if len(label) > 1:
    #                 token = self.random.choice(label)
    #             else:
    #                 token = label[0] - 1
    #             return full_dict.unsqueeze_(1), token.unsqueeze(0)
    #     elif labels.size(1) == 1:
    #         self._warn_once(
    #             'ranking_unique',
    #             '[ Training using unique labels in batch as cands. ]')
    #         # use unique if input is 1D
    #         cands, label_inds = labels.unique(return_inverse=True)
    #         cands.unsqueeze_(1)
    #         label_inds.squeeze_(1)
    #         return cands, label_inds
    #     else:
    #         self._warn_once(
    #             'ranking_batch',
    #             '[ Training using other labels in batch as cands. ]')
    #         return labels, labels.new(range(labels.size(0)))

    def _build_mems(self, mems):
        """Build memory tensors.

        During building, will add time features to the memories if enabled.

        :param: list of length batchsize containing inner lists of 1D tensors
                containing the individual memories for each row in the batch.

        :returns: 3d padded tensor of memories (bsz x num_mems x seqlen)
        """
        if mems is None:
            return None
        bsz = len(mems)
        if bsz == 0:
            return None

        num_mems = max(len(mem) for mem in mems)
        if num_mems == 0 or self.memsize <= 0:
            return None
        elif num_mems > self.memsize:
            # truncate to memsize most recent memories
            num_mems = self.memsize
            mems = [mem[-self.memsize:] for mem in mems]

        try:
            seqlen = max(len(m) for mem in mems for m in mem)
            if self.use_time_features:
                seqlen += 1  # add time token to each sequence
        except ValueError:
            return None

        padded = torch.LongTensor(bsz, num_mems, seqlen).fill_(0)

        for i, mem in enumerate(mems):
            # tf_offset = len(mem) - 1
            for j, m in enumerate(mem):
                padded[i, j, :len(m)] = m
                # if self.use_time_features:
                #     padded[i, j, -1] = self.dict[self._time_feature(tf_offset - j)]

        # NOTE: currently below we are adding tf's to every memory,
        # including emtpy ones. above commented-out code adds only to filled
        # ones but is significantly slower to run.
        if self.use_time_features:
            nm = num_mems - 1
            for i in range(num_mems):
                # put lowest time feature in most recent memory
                padded[:, nm - i, -1] = self.dict[self._time_feature(i)]

        if self.use_cuda:
            padded = padded.cuda()

        return padded

    # def train_step(self, batch):
    #     """Train on a single batch of examples."""
    #     if batch.text_vec is None:
    #         return
    #     batchsize = batch.text_vec.size(0)
    #     self.model.train()
    #     self.optimizer.zero_grad()
    #     mems = self._build_mems(batch.memory_vecs)
    #
    #     cands, label_inds = self._build_train_cands(batch.label_vec,
    #                                                 batch.candidate_vecs)
    #
    #     scores = self.model(batch.text_vec, mems, cands)
    #     loss = self.rank_loss(scores, label_inds)
    #
    #     self.metrics['loss'] += loss.item()
    #     self.metrics['batches'] += batchsize
    #     _, ranks = scores.sort(1, descending=True)
    #     for b in range(batchsize):
    #         rank = (ranks[b] == label_inds[b]).nonzero().item()
    #         self.metrics['rank'] += 1 + rank
    #     loss.backward()
    #     self.update_params()
    #
    #     # get predictions but not full rankings--too slow to get hits@1 score
    #     preds = [self._v2t(cands[row[0]]) for row in ranks]
    #     return Output(preds)
    #
    # def _build_label_cands(self, batch):
    #     """Convert batch.candidate_vecs to 3D padded vector."""
    #     if not batch.candidates:
    #         return None, None
    #     cand_inds = [i for i in range(len(batch.candidates))
    #                  if batch.candidates[i]]
    #     cands = padded_3d(batch.candidate_vecs, pad_idx=self.NULL_IDX,
    #                       use_cuda=self.use_cuda)
    #     return cands, cand_inds
    #
    # def eval_step(self, batch):
    #     """Evaluate a single batch of examples."""
    #     if batch.text_vec is None:
    #         return
    #     batchsize = batch.text_vec.size(0)
    #     self.model.eval()
    #
    #     mems = self._build_mems(batch.memory_vecs)
    #     cands, cand_inds = self._build_label_cands(batch)
    #     scores = self.model(batch.text_vec, mems, cands)
    #
    #     self.metrics['batches'] += batchsize
    #     _, ranks = scores.sort(1, descending=True)
    #
    #     # calculate loss and mean rank
    #     if batch.label_vec is not None and cands is not None:
    #         label_inds = []
    #         noproblems = True
    #         for b in range(batchsize):
    #             label_ind = (cands[b] == batch.label_vec[b]).all(1).nonzero()
    #             if label_ind.size(0) == 0:
    #                 li = label_ind.item()
    #             else:
    #                 # don't calculate loss
    #                 self._warn_once(
    #                     'eval_loss',
    #                     '[ WARNING: duplicate multitoken candidates detected. '
    #                     'This batch\'s loss and mean_rank calculation will be '
    #                     'skipped, skewing their totals. hits@k, accuracy, and '
    #                     'f1 will be unaffected so use those instead.')
    #                 noproblems = False
    #                 continue
    #             label_inds.append(label_ind)
    #             rank = (ranks[b] == li).nonzero().item()
    #             self.metrics['rank'] += 1 + rank
    #
    #         if noproblems:
    #             label_inds = torch.cat(label_inds, dim=0).squeeze(1)
    #             loss = self.rank_loss(scores, label_inds)
    #             self.metrics['loss'] += loss.item()
    #
    #     preds, cand_preds = None, None
    #     if batch.candidates:
    #         cand_preds = [[batch.candidates[b][i.item()] for i in row]
    #                       for b, row in enumerate(ranks)]
    #         preds = [row[0] for row in cand_preds]
    #     else:
    #         cand_preds = [[self.dict[i.item()] for i in row]
    #                       for row in ranks]
    #         preds = [row[0] for row in cand_preds]
    #
    #     return Output(preds, cand_preds)
